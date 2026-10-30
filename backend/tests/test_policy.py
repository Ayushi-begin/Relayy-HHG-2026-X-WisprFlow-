import datetime as dt

from models import Payment, PaymentStatus
from swarm.policy import eligibility_error, enforce
from swarm.state import ActionProposal, PaymentRecord, ResearchResult, TicketData, TicketState, TriageResult


NOW = dt.datetime(2026, 10, 30, tzinfo=dt.timezone.utc)


def evidence(**overrides):
    result = {
        "purchase_type": "addon",
        "charge_at": (NOW - dt.timedelta(days=14)).isoformat(),
        "verified_at": (NOW - dt.timedelta(minutes=1)).isoformat(),
        "workspace_id": "workspace-1",
        "billing_period": "2026-10",
        "product_id": "addon-analytics",
        "eligibility_reason": "requested_within_window",
    }
    result.update(overrides)
    return result


def payment(**overrides):
    values = {
        "id": 10,
        "customer_id": 7,
        "amount": 30.0,
        "amount_minor": 3000,
        "currency": "usd",
        "date": NOW - dt.timedelta(days=14),
        "status": PaymentStatus.succeeded,
        "description": "Analytics add-on",
        "provider_account_id": "acct_demo",
        "stripe_charge_id": "ch_demo",
        "livemode": False,
        "binding_verified_at": NOW - dt.timedelta(minutes=1),
        "eligibility_evidence": evidence(),
    }
    values.update(overrides)
    return Payment(**values)


def state_for(record: PaymentRecord, *, provenance=None):
    return TicketState(
        ticket=TicketData(id=1, customer_id=7, subject="Refund", body="Please refund", created_at=NOW),
        triage=TriageResult(
            category="refund", urgency="high", sentiment="neutral", language="en", is_suspicious=False,
            confidence=0.9, summary="Refund request",
        ),
        research=ResearchResult(payments=[record]),
        provenance=provenance or {},
    )


def proposal(**overrides):
    values = {
        "decision": "auto_refund",
        "reply_draft": "Your refund is being processed.",
        "holding_reply": "A specialist will review this.",
        "refund_amount": 30.0,
        "payment_id": 10,
        "refund_amount_minor": 3000,
        "reasoning": "Eligible add-on.",
        "confidence": 0.9,
    }
    values.update(overrides)
    return ActionProposal(**values)


def record_from(payment_row: Payment) -> PaymentRecord:
    return PaymentRecord.model_validate(payment_row.model_dump())


def test_eligibility_window_includes_exactly_fourteen_days():
    assert eligibility_error(payment(), NOW) is None
    assert eligibility_error(payment(), NOW + dt.timedelta(microseconds=1)) == "refund_window_expired"


def test_eligibility_rejects_monthly_and_enterprise():
    assert eligibility_error(payment(eligibility_evidence=evidence(purchase_type="monthly")), NOW) == "monthly_purchase_not_refundable"
    assert eligibility_error(payment(eligibility_evidence=evidence(purchase_type="enterprise")), NOW) == "enterprise_requires_escalation"


def test_duplicate_needs_explicit_server_verified_peer_evidence():
    same_amount = payment(eligibility_evidence=evidence(eligibility_reason="duplicate"))
    assert eligibility_error(same_amount, NOW) == "unverified_duplicate_evidence"
    confirmed = payment(
        eligibility_evidence=evidence(
            eligibility_reason="confirmed_duplicate",
            duplicate_peer_payment_id=9,
            duplicate_peer_settled=True,
            duplicate_peer_same_workspace_period_product=True,
            duplicate_newer_charge=True,
        )
    )
    assert eligibility_error(confirmed, NOW + dt.timedelta(days=30)) is None


def test_policy_requires_research_payment_exact_integer_usd_full_amount():
    row = payment()
    result = enforce(state_for(record_from(row)), proposal(), payment_lookup=lambda _id, _customer: row)
    assert result.decision == "auto_refund"
    assert result.payment_id == 10
    assert result.refund_amount_minor == 3000

    result = enforce(
        state_for(record_from(row)), proposal(refund_amount_minor=2999), payment_lookup=lambda _id, _customer: row
    )
    assert result.decision == "escalate"
    assert "invalid_refund_money" in result.rules_fired


def test_policy_rejects_payment_not_returned_by_scoped_research():
    row = payment()
    other = record_from(payment(id=11))
    result = enforce(state_for(other), proposal(), payment_lookup=lambda _id, _customer: row)
    assert result.decision == "escalate"
    assert "payment_not_in_research" in result.rules_fired


def test_simulated_high_confidence_action_has_no_automatic_authority():
    row = payment()
    state = state_for(
        record_from(row),
        provenance={"triage": "simulated", "research": "simulated", "action": "simulated"},
    )
    result = enforce(state, proposal(confidence=1.0), payment_lookup=lambda _id, _customer: row)
    assert result.decision == "escalate"
    assert "unverified_provenance" in result.rules_fired
