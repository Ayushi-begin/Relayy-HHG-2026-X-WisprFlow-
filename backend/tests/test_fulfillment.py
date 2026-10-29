"""Deterministic fulfillment tests: no model or provider network is used."""

import datetime as dt
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import config
from fulfillment import service, store
from fulfillment.contracts import ChargeInfo, ProviderResult
from models import Customer, DeliveryOperation, OperationAttempt, Payment, PaymentStatus, Ticket, TicketRun, TicketStatus, Tier


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(service, "engine", engine)
    monkeypatch.setattr(config, "DELIVERY_MODE", "test")
    monkeypatch.setattr(config, "DEMO_OPERATOR_TOKEN", "x" * 40)
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(config, "RESEND_FROM", "demo@example.test")
    monkeypatch.setattr(config, "RESEND_ALLOWED_RECIPIENTS", {"approved@example.test"})
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_example")
    monkeypatch.setattr(config, "STRIPE_ACCOUNT_ID", "acct_demo")
    with Session(engine) as session:
        customer = Customer(id=1, name="Demo", email="fictional@example.test", plan="Pro", tier=Tier.pro,
                            signup_date=dt.date(2026, 1, 1), demo_recipient="approved@example.test",
                            stripe_customer_id="cus_demo", provider_account_id="acct_demo", binding_verified_at=store.now())
        session.add(customer)
        session.add(Ticket(id=1, customer_id=1, subject="Question", body="Help", created_at=store.now(),
                           current_run_id="run1", status=TicketStatus.pending))
        session.add(TicketRun(id="run1", ticket_id=1, run_number=1, input_snapshot={}, started_at=store.now(),
                              authorized_plan={"source": "approved"}))
        session.commit()
    yield engine
    engine.dispose()


def email(session, **kwargs):
    request = {"from_address": "demo@example.test", "to": "approved@example.test", "subject": "Re: Question", "text": "A safe answer."}
    return store.freeze(session, ticket_id=1, run_id="run1", kind="email", request=request,
                        logical_key="email:ticket:1", **kwargs)


class FakeEmail:
    def __init__(self, result=None):
        self.result = result or ProviderResult("accepted", "accepted", "email_1")
        self.sends = []
        self.reads = []

    async def send(self, request, key):
        self.sends.append((request, key))
        return self.result

    async def retrieve(self, identifier):
        self.reads.append(identifier)
        return self.result


def test_freeze_is_stable_and_claim_is_conditional(database):
    with Session(database) as session:
        op = email(session)
        again = email(session)
        assert op.id == again.id and op.idempotency_key == again.idempotency_key
        session.commit()
        claim = store.claim(session, op, "send")
        assert claim
        assert session.exec(select(OperationAttempt)).one().attempt_number == 1


def test_same_payment_reservation_cannot_move_to_another_ticket(database):
    with Session(database) as session:
        store.freeze(session, ticket_id=1, run_id="run1", kind="refund", request={"amount_minor": 3000}, logical_key="refund:acct:charge")
        with pytest.raises(ValueError, match="frozen operation"):
            store.freeze(session, ticket_id=2, run_id="run2", kind="refund", request={"amount_minor": 3000}, logical_key="refund:acct:charge")


@pytest.mark.asyncio
async def test_accepted_email_has_evidence_and_does_not_resend(database):
    with Session(database) as session:
        identifier = email(session).id
        session.commit()
    provider = FakeEmail()
    await service.process_operation(identifier, email_provider=provider)
    await service.process_operation(identifier, email_provider=provider)
    assert len(provider.sends) == 1
    with Session(database) as session:
        assert session.get(DeliveryOperation, identifier).state == "accepted"
        ticket = session.get(Ticket, 1)
        assert ticket.status == TicketStatus.resolved
        assert ticket.final_reply == "A safe answer."


@pytest.mark.asyncio
async def test_unknown_send_reuses_frozen_key(database):
    with Session(database) as session:
        op = email(session)
        identifier, key = op.id, op.idempotency_key
        session.commit()
    provider = FakeEmail(ProviderResult("unknown", "unknown", error_code="timeout"))
    await service.process_operation(identifier, email_provider=provider)
    with Session(database) as session:
        op = session.get(DeliveryOperation, identifier)
        op.next_check_at = store.now()
        session.add(op)
        session.commit()
    provider.result = ProviderResult("accepted", "accepted", "email_1")
    await service.process_operation(identifier, email_provider=provider)
    assert [entry[1] for entry in provider.sends] == [key, key]


@pytest.mark.asyncio
async def test_expired_unknown_email_does_not_blind_resend(database):
    with Session(database) as session:
        op = email(session)
        op.state, op.first_attempt_at = "unknown", store.now() - dt.timedelta(hours=25)
        op.retry_deadline = store.now() - dt.timedelta(hours=2)
        session.add(op)
        session.commit()
        identifier = op.id
    provider = FakeEmail()
    await service.process_operation(identifier, email_provider=provider)
    assert not provider.sends
    with Session(database) as session:
        assert session.get(DeliveryOperation, identifier).error_code == "idempotency_expired"


@pytest.mark.asyncio
async def test_expired_unknown_email_with_id_can_retrieve(database):
    with Session(database) as session:
        op = email(session)
        op.state, op.provider_id = "unknown", "email_1"
        op.first_attempt_at, op.retry_deadline = store.now() - dt.timedelta(days=2), store.now() - dt.timedelta(days=1)
        session.add(op)
        session.commit()
        identifier = op.id
    provider = FakeEmail()
    await service.process_operation(identifier, email_provider=provider)
    assert provider.reads == ["email_1"] and not provider.sends


@pytest.mark.asyncio
async def test_pending_dependency_blocks_email(database):
    with Session(database) as session:
        refund = store.freeze(session, ticket_id=1, run_id="run1", kind="refund", request={}, logical_key="refund:demo")
        refund.state = "pending"
        op = email(session, dependency_id=refund.id)
        session.add(refund)
        session.commit()
        identifier = op.id
    provider = FakeEmail()
    await service.process_operation(identifier, email_provider=provider)
    assert not provider.sends


def test_restart_preserves_unknown_and_accepted_records(database):
    with Session(database) as session:
        op = email(session)
        op.state = "executing"
        op.claim_token = "old"
        session.add(op)
        session.commit()
        identifier = op.id
    service.recover_operations()
    with Session(database) as session:
        op = session.get(DeliveryOperation, identifier)
        assert op.state == "unknown" and op.claim_token is None


def test_human_approval_never_creates_refused_refund(database):
    with Session(database) as session:
        ticket = session.get(Ticket, 1)
        state = SimpleNamespace(action=SimpleNamespace(decision="auto_refund", refund_amount_minor=45000), provenance={})
        operations = service.authorize(session, ticket, state, "A specialist will review.", "approved")
        assert [op.kind for op in operations] == ["email"]
        assert ticket.manual_followup_required


def test_simulated_automatic_action_is_blocked(database):
    with Session(database) as session:
        ticket = session.get(Ticket, 1)
        state = SimpleNamespace(action=SimpleNamespace(decision="auto_resolve"), provenance={"triage": "simulated"})
        operations = service.authorize(session, ticket, state, "A safe answer.", "ai")
        assert operations[0].state == "blocked"
        assert operations[0].error_code == "model_provenance_unverified"


@pytest.mark.parametrize("reply", ["", "   ", "x" * 10001])
def test_invalid_human_reply_cannot_be_authorized(database, reply):
    with Session(database) as session:
        with pytest.raises(ValueError):
            service.authorize(session, session.get(Ticket, 1), SimpleNamespace(action=None), reply, "edited")


def test_seed_address_is_not_a_delivery_fallback(database):
    with Session(database) as session:
        customer = session.get(Customer, 1)
        customer.demo_recipient = None
        session.add(customer)
        operations = service.authorize(session, session.get(Ticket, 1), SimpleNamespace(action=None), "Safe reply", "approved")
        assert operations[0].state == "blocked"
        assert operations[0].request["to"] == ""
        assert "retry" not in service.serialize_operation(operations[0])["allowed_actions"]


def refund_fixture(session):
    payment = Payment(id=1, customer_id=1, amount=30.0, date=store.now(), status=PaymentStatus.succeeded,
                      description="Test add-on", amount_minor=3000, currency="usd", livemode=False,
                      stripe_charge_id="ch_demo", provider_account_id="acct_demo", binding_verified_at=store.now(),
                      eligibility_evidence={"purchase_type": "addon", "charge_at": (store.now() - dt.timedelta(days=1)).isoformat(),
                                            "verified_at": (store.now() - dt.timedelta(minutes=1)).isoformat(),
                                            "workspace_id": "ws_demo", "billing_period": "2026-10", "product_id": "addon",
                                            "eligibility_reason": "operator_verified_demo_fixture"})
    session.add(payment)
    session.flush()
    request = {"local_payment_id": 1, "charge_id": "ch_demo", "customer_id": "cus_demo", "account_id": "acct_demo", "currency": "usd", "amount_minor": 3000}
    return store.freeze(session, ticket_id=1, run_id="run1", kind="refund", request=request, logical_key="refund:acct_demo:ch_demo")


class FakeStripe:
    def __init__(self, result=None):
        self.result = result or ProviderResult("succeeded", "succeeded", "re_demo")
        self.refunds = []
        self.refunded = 0

    async def inspect_charge(self, identifier):
        return ChargeInfo(identifier, "cus_demo", "acct_demo", 3000, "usd", False, True, True, self.refunded)

    async def list_refunds(self, identifier):
        return []

    async def refund(self, request, key):
        self.refunds.append((request, key))
        if self.result.state == "succeeded":
            self.refunded = 3000
        return self.result

    async def retrieve(self, identifier):
        return self.result


@pytest.mark.asyncio
async def test_refund_success_email_failure_recovers_email_only(database):
    with Session(database) as session:
        refund = refund_fixture(session)
        reply = email(session, dependency_id=refund.id)
        session.commit()
        refund_id, email_id = refund.id, reply.id
    stripe, resend = FakeStripe(), FakeEmail(ProviderResult("failed", "rejected", error_code="email_rejected"))
    await service.process_operation(refund_id, refund_provider=stripe)
    await service.process_operation(email_id, email_provider=resend)
    with Session(database) as session:
        ticket = session.get(Ticket, 1)
        assert ticket.fulfillment_state == "partial"
        assert ticket.final_reply is None
        assert session.get(Payment, 1).status == PaymentStatus.refunded
        version = ticket.version
    service.request_recovery(1, email_id, version, "retry")
    resend.result = ProviderResult("accepted", "accepted", "email_final")
    await service.process_operation(email_id, email_provider=resend)
    assert len(stripe.refunds) == 1
    with Session(database) as session:
        assert session.get(Ticket, 1).fulfillment_state == "complete"
        assert session.get(DeliveryOperation, refund_id).provider_id == "re_demo"


@pytest.mark.asyncio
async def test_pending_refund_never_claims_success(database):
    with Session(database) as session:
        refund = refund_fixture(session)
        reply = email(session, dependency_id=refund.id)
        session.commit()
        refund_id, email_id = refund.id, reply.id
    stripe = FakeStripe(ProviderResult("pending", "pending", "re_pending"))
    resend = FakeEmail()
    await service.process_operation(refund_id, refund_provider=stripe)
    await service.process_operation(email_id, email_provider=resend)
    assert not resend.sends
    with Session(database) as session:
        assert session.get(Payment, 1).status == PaymentStatus.succeeded
        assert session.get(Ticket, 1).status == TicketStatus.pending


@pytest.mark.asyncio
async def test_live_credentials_block_before_money_call(database, monkeypatch):
    with Session(database) as session:
        refund = refund_fixture(session)
        session.commit()
        identifier = refund.id
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_live_do_not_use")
    stripe = FakeStripe()
    await service.process_operation(identifier, refund_provider=stripe)
    assert not stripe.refunds
    with Session(database) as session:
        assert session.get(DeliveryOperation, identifier).state == "blocked"


@pytest.mark.asyncio
async def test_frozen_request_mutation_blocks_send(database):
    with Session(database) as session:
        op = email(session)
        op.request = dict(op.request, text="Changed after authorization")
        session.add(op)
        session.commit()
        identifier = op.id
    provider = FakeEmail()
    await service.process_operation(identifier, email_provider=provider)
    assert not provider.sends
    with Session(database) as session:
        assert session.get(DeliveryOperation, identifier).error_code == "request_changed"
