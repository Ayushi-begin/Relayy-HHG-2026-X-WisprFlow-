"""One durable fulfillment path for automatic actions and approved replies.

The database reserves intent; providers execute effects. Finite provider idempotency
windows cannot guarantee exactly-once delivery. Ambiguous expired attempts stop.
"""

import asyncio
import datetime as dt
import logging

from sqlalchemy import update
from sqlmodel import Session, select

import config
from db import engine
from events import bus
from models import Customer, DeliveryOperation, OperationAttempt, Payment, PaymentStatus, Ticket, TicketEvent, TicketRun, TicketStatus
from fulfillment import store

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 6
MAX_SCHEDULED_CHECKS = 12


def readiness() -> dict:
    email_configured = bool(config.RESEND_API_KEY and config.RESEND_FROM and config.RESEND_ALLOWED_RECIPIENTS)
    refund_configured = bool(config.STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_")) and config.STRIPE_ACCOUNT_ID)
    return {
        "delivery_mode": config.DELIVERY_MODE,
        "operator_required": bool(config.DEMO_OPERATOR_TOKEN) or config.DELIVERY_MODE != "disabled",
        "email_configured": email_configured,
        "refund_configured": refund_configured,
        "provider_access_verified": False,
        "providers": {name: {"configured": configured, "ready": False,
                              "state": "configured_not_verified" if configured else "missing_configuration"}
                      for name, configured in (("resend", email_configured), ("stripe", refund_configured))},
    }


def _email_block(request: dict, session: Session | None = None) -> str | None:
    if config.DELIVERY_MODE != "test":
        return "delivery_disabled"
    if len(config.DEMO_OPERATOR_TOKEN) < 32:
        return "operator_not_configured"
    if not config.RESEND_API_KEY or not config.RESEND_FROM:
        return "email_not_configured"
    target = request.get("to", "")
    if not target or target.lower() not in config.RESEND_ALLOWED_RECIPIENTS:
        return "recipient_not_approved"
    if session is not None and session.exec(select(Customer.id).where(Customer.email == target.lower()).limit(1)).first():
        return "fictional_seed_recipient"
    if request.get("from_address") != config.RESEND_FROM:
        return "sender_changed"
    return None


def _refund_block(session: Session, ticket: Ticket, request: dict) -> str | None:
    if config.DELIVERY_MODE != "test":
        return "delivery_disabled"
    if len(config.DEMO_OPERATOR_TOKEN) < 32:
        return "operator_not_configured"
    if not config.STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_")) or not config.STRIPE_ACCOUNT_ID:
        return "stripe_test_not_configured"
    payment = session.get(Payment, request.get("local_payment_id"))
    customer = session.get(Customer, ticket.customer_id)
    if not payment or not customer or payment.customer_id != ticket.customer_id:
        return "payment_not_owned"
    if not payment.binding_verified_at or not customer.binding_verified_at:
        return "binding_unverified"
    if (payment.provider_account_id != config.STRIPE_ACCOUNT_ID
            or customer.provider_account_id != config.STRIPE_ACCOUNT_ID
            or request.get("account_id") != config.STRIPE_ACCOUNT_ID
            or not payment.stripe_charge_id or not customer.stripe_customer_id
            or request.get("charge_id") != payment.stripe_charge_id
            or request.get("customer_id") != customer.stripe_customer_id):
        return "payment_binding_mismatch"
    amount = request.get("amount_minor")
    if (type(amount) is not int or not 0 < amount <= 5000 or payment.amount_minor != amount
            or payment.currency != "usd" or request.get("currency") != "usd" or payment.livemode is not False):
        return "invalid_refund_money"
    if payment.status not in (PaymentStatus.succeeded, PaymentStatus.refunded):
        return "payment_not_successful"
    from swarm.policy import eligibility_error
    return eligibility_error(payment, ticket.created_at)


def authorize(session: Session, ticket: Ticket, state, reply: str, authorization_source: str) -> list[DeliveryOperation]:
    """Freeze intent within the caller's version-claim transaction; never commit here."""
    if not reply or not reply.strip() or len(reply) > 10000:
        raise ValueError("A nonblank reply of at most 10000 characters is required.")
    run = session.get(TicketRun, ticket.current_run_id)
    if not run:
        raise ValueError("A current run is required.")
    if authorization_source not in ("ai", "approved", "edited"):
        raise ValueError("Unknown authorization source.")
    request = {"from_address": config.RESEND_FROM, "to": "", "subject": f"Re: {ticket.subject}", "text": reply.strip()}
    customer = session.get(Customer, ticket.customer_id)
    if customer:
        request["to"] = customer.demo_recipient or ""
    block = _email_block(request, session)
    action = state.action
    if authorization_source == "ai":
        if not action or action.decision not in ("auto_resolve", "auto_refund"):
            raise ValueError("No automatic action is authorized.")
        if any(getattr(state, "provenance", {}).get(stage) != "real_model" for stage in ("triage", "research", "action")):
            block = "model_provenance_unverified"
    refund = None
    # Human review authorizes a reply ONLY. It never recovers a refused proposal.
    if authorization_source == "ai" and action.decision == "auto_refund":
        payment = session.get(Payment, getattr(action, "payment_id", None))
        refund_request = {
            "local_payment_id": getattr(action, "payment_id", None),
            "charge_id": payment.stripe_charge_id if payment else "",
            "customer_id": customer.stripe_customer_id if customer else "",
            "account_id": config.STRIPE_ACCOUNT_ID, "currency": "usd",
            "amount_minor": getattr(action, "refund_amount_minor", None),
        }
        researched = {p.id for p in state.research.payments} if state.research else set()
        refund_block = block or ("payment_not_researched" if not payment or payment.id not in researched else None)
        refund_block = refund_block or _refund_block(session, ticket, refund_request)
        # A successful-refund message is canonical and cannot promise bank posting times.
        amount = refund_request["amount_minor"]
        if type(amount) is int and amount > 0:
            request["text"] = f"We confirmed a USD {amount / 100:.2f} Stripe test refund for your mapped test payment. This demonstration does not move real money."
        key = f"refund:{config.STRIPE_ACCOUNT_ID}:{refund_request['charge_id']}" if refund_request["charge_id"] else f"refund:unbound:{ticket.id}"
        refund = store.freeze(session, ticket_id=ticket.id, run_id=run.id, kind="refund", request=refund_request,
                              logical_key=key, blocked=refund_block)
        block = block or refund_block
    email = store.freeze(session, ticket_id=ticket.id, run_id=run.id, kind="email", request=request,
                         logical_key=f"email:ticket:{ticket.id}", dependency_id=refund.id if refund else None, blocked=block)
    operations = [refund, email] if refund else [email]
    run.authorized_plan = {"source": authorization_source, "operation_ids": [op.id for op in operations],
                           "decision": action.decision if action else "reviewed_reply", "reply": request["text"]}
    ticket.fulfillment_state = "blocked" if any(op.state == "blocked" for op in operations) else "pending"
    ticket.status = TicketStatus.escalated if ticket.fulfillment_state == "blocked" else TicketStatus.pending
    ticket.final_reply = None
    ticket.manual_followup_required = authorization_source != "ai" or ticket.fulfillment_state == "blocked"
    session.add(run)
    session.add(ticket)
    return operations


def _allowed_actions(op: DeliveryOperation, session: Session | None = None) -> list[str]:
    if op.state in ("pending", "unknown") or (op.kind == "refund" and op.state == "succeeded"):
        return ["reconcile"]
    if op.state == "failed" and op.provider_id:
        # A known rejected/bounced object cannot become a fresh send/refund intent.
        return ["reconcile"]
    if op.state in ("failed", "blocked") and (not op.retry_deadline or store.aware(op.retry_deadline) > store.now()):
        if op.send_attempt_count >= MAX_ATTEMPTS:
            return []
        if op.error_code in ("model_provenance_unverified", "recipient_not_approved", "fictional_seed_recipient", "payment_not_researched", "payment_binding_mismatch", "binding_unverified", "invalid_refund_money", "sender_changed", "request_changed", "invalid_frozen_request"):
            return []
        if op.request_hash != store.fingerprint(op.request):
            return []
        if op.kind == "email":
            if op.dependency_id:
                dependency = session.get(DeliveryOperation, op.dependency_id) if session else None
                if not dependency or dependency.state != "succeeded":
                    return []
            return [] if _email_block(op.request, session) else ["retry"]
        ticket = session.get(Ticket, op.ticket_id) if session else None
        return ["retry"] if ticket and not _refund_block(session, ticket, op.request) else []
    return []


def serialize_operation(op: DeliveryOperation, session: Session | None = None) -> dict:
    return {
        "id": op.id, "kind": op.kind, "state": op.state, "provider_status": op.provider_status,
        "provider_id": op.provider_id, "amount_minor": op.request.get("amount_minor"),
        "currency": op.request.get("currency"), "test_mode": True,
        "recipient": op.request.get("to"), "updated_at": op.updated_at.isoformat(),
        "error_code": op.error_code, "error_message": op.error_message,
        "allowed_actions": _allowed_actions(op, session),
    }


def request_recovery(ticket_id: int, op_id: str, expected_version: int, action: str) -> None:
    with Session(engine) as session:
        ticket, op = session.get(Ticket, ticket_id), session.get(DeliveryOperation, op_id)
        if not ticket or not op or op.ticket_id != ticket_id or ticket.current_run_id != op.run_id:
            raise ValueError("Operation not found on the current ticket run.")
        if ticket.version != expected_version or action not in _allowed_actions(op, session):
            raise ValueError("Recovery is unavailable or the ticket changed. Refresh the ticket.")
        if action == "retry":
            error = _email_block(op.request, session) if op.kind == "email" else _refund_block(session, ticket, op.request)
            if error:
                raise ValueError(f"Recovery remains blocked: {error}.")
            if op.request_hash != store.fingerprint(op.request):
                raise ValueError("Frozen request changed.")
        result = session.exec(update(Ticket).where(Ticket.id == ticket_id, Ticket.version == expected_version).values(
            version=expected_version + 1, status=TicketStatus.pending, fulfillment_state="pending", updated_at=store.now()))
        if result.rowcount != 1:
            raise ValueError("Another action changed this ticket.")
        op.next_check_at = store.now()
        op.manual_check_requested = action == "reconcile"
        if action == "retry":
            op.state = "queued"
        elif op.state == "failed":
            op.state = "unknown"
        op.version += 1
        op.updated_at = store.now()
        session.add(op)
        run_id, version = ticket.current_run_id, expected_version + 1
        session.commit()
    bus.publish_status(ticket_id, TicketStatus.pending, run_id=run_id, version=version)


def _refresh_ticket(session: Session, ticket: Ticket) -> None:
    operations = session.exec(select(DeliveryOperation).where(DeliveryOperation.ticket_id == ticket.id)).all()
    email = next((op for op in operations if op.kind == "email"), None)
    refund = next((op for op in operations if op.kind == "refund"), None)
    complete = email and email.state == "accepted" and (not refund or refund.state == "succeeded")
    if complete:
        ticket.status = TicketStatus.resolved
        ticket.fulfillment_state = "complete"
        ticket.final_reply = email.request["text"]
        run = session.get(TicketRun, ticket.current_run_id)
        source = (run.authorized_plan or {}).get("source") if run else None
        ticket.resolution = ("auto_refunded" if refund else "auto_resolved") if source == "ai" else source
    elif any(op.state in ("blocked", "failed", "canceled") for op in operations):
        ticket.status = TicketStatus.escalated
        ticket.fulfillment_state = "partial" if refund and refund.state == "succeeded" else "blocked"
        ticket.manual_followup_required = True
    else:
        ticket.status = TicketStatus.pending
        ticket.fulfillment_state = "partial" if refund and refund.state == "succeeded" else "pending"
    if refund and refund.state == "succeeded":
        payment = session.get(Payment, refund.request.get("local_payment_id"))
        if payment:
            payment.status = PaymentStatus.refunded
            session.add(payment)
    elif refund and refund.state in ("failed", "canceled"):
        # Preserve the audit observation, but do not keep claiming current success.
        payment = session.get(Payment, refund.request.get("local_payment_id"))
        if payment and payment.status == PaymentStatus.refunded:
            payment.status = PaymentStatus.succeeded
            session.add(payment)
        ticket.manual_followup_required = True
        ticket.resolution = None
    ticket.updated_at = store.now()
    ticket.version += 1
    session.add(ticket)


def _reserve_send(operation_id: str, claim_token: str) -> bool:
    """Persist the effect-attempt budget before a provider submission, not a read."""
    with Session(engine) as session:
        updated = session.exec(update(DeliveryOperation).where(
            DeliveryOperation.id == operation_id, DeliveryOperation.claim_token == claim_token,
            DeliveryOperation.state == "executing", DeliveryOperation.send_attempt_count < MAX_ATTEMPTS,
        ).values(send_attempt_count=DeliveryOperation.send_attempt_count + 1))
        if updated.rowcount != 1:
            return False
        session.exec(update(OperationAttempt).where(OperationAttempt.claim_token == claim_token).values(kind="send"))
        session.commit()
        return True


async def _refund_submission(provider, request: dict, operation_id: str, token: str, key: str,
                             deadline: dt.datetime, reconciling: bool):
    from fulfillment.contracts import ProviderResult, RefundRequest

    charge = await provider.inspect_charge(request["charge_id"])
    valid = (charge.account_id == request["account_id"] == config.STRIPE_ACCOUNT_ID
             and charge.customer_id == request["customer_id"] and charge.livemode is False
             and charge.currency == request["currency"] == "usd"
             and charge.amount_minor == request["amount_minor"] and charge.paid and charge.captured)
    if not valid:
        return ProviderResult("blocked", "blocked", error_code="provider_charge_mismatch", error_message="Provider charge does not match the verified test binding.")
    refunds = await provider.list_refunds(request["charge_id"])
    matches = [item for item in refunds if item.get("metadata", {}).get("relayy_operation_id") == operation_id
               and item.get("amount") == request["amount_minor"] and item.get("currency") == "usd"
               and item.get("charge_id") == request["charge_id"]]
    if len(matches) == 1:
        return await provider.retrieve(matches[0]["id"])
    if matches or (reconciling and (charge.amount_refunded or store.now() >= deadline)):
        return ProviderResult("unknown", "unknown", error_code="refund_reconciliation_required", error_message="Refund evidence is ambiguous or the safe retry window expired.")
    if charge.amount_refunded or refunds:
        return ProviderResult("blocked", "blocked", error_code="existing_refund", error_message="This charge already has a different refund or refund reservation.")
    if store.now() >= deadline:
        return ProviderResult("unknown", "unknown", error_code="idempotency_expired", error_message="The safe refund retry window expired. Investigate provider evidence.")
    if not _reserve_send(operation_id, token):
        return ProviderResult("unknown", "unknown", error_code="send_attempt_limit", error_message="The send attempt limit is reached. Read-only reconciliation remains available.")
    return await provider.refund(RefundRequest(**{k: v for k, v in request.items() if k != "local_payment_id"}, operation_id=operation_id), key)


async def process_operation(operation_id: str, email_provider=None, refund_provider=None) -> None:
    """Execute or reconcile one durable operation; adapters are injectable for tests."""
    from fulfillment.contracts import EmailRequest, ProviderError, ProviderResult
    from integrations.resend import ResendAdapter
    from integrations.stripe import StripeAdapter

    with Session(engine) as session:
        op = session.get(DeliveryOperation, operation_id)
        if not op or op.state not in ("queued", "pending", "unknown", "succeeded"):
            return
        if op.state == "succeeded" and op.kind != "refund":
            return
        if op.next_check_at and store.aware(op.next_check_at) > store.now():
            return
        if op.dependency_id:
            dependency = session.get(DeliveryOperation, op.dependency_id)
            if not dependency or dependency.state != "succeeded":
                return
        ticket = session.get(Ticket, op.ticket_id)
        if not ticket or ticket.current_run_id != op.run_id:
            return
        previous_state, previous_provider_status = op.state, op.provider_status
        reconciling = op.state in ("pending", "unknown", "succeeded") or bool(op.provider_id)
        # Expired queued retries cannot become new POSTs after the retention window.
        expired = bool(op.retry_deadline and store.aware(op.retry_deadline) <= store.now())
        if op.state == "queued" and expired:
            reconciling = True
        claimed = store.claim(session, op, "reconcile" if reconciling else "send")
        if not claimed:
            return
        token, attempt = claimed
        attempt_id = attempt.id
        request, kind, key = dict(op.request), op.kind, op.idempotency_key
        provider_id, original_hash = op.provider_id, op.request_hash
        ticket_id, run_id = op.ticket_id, op.run_id
        deadline = store.aware(op.retry_deadline)
        first_attempt = op.attempt_count
        # Reconciliation is a read of an already submitted intent, not a new authorization.
        blocked = None if reconciling and provider_id else (
            _email_block(request, session) if kind == "email" else _refund_block(session, ticket, request)
        )
    owned = []
    try:
        if original_hash != store.fingerprint(request):
            result = ProviderResult("blocked", "blocked", error_code="request_changed", error_message="Frozen request changed; investigate manually.")
        elif blocked:
            result = ProviderResult("blocked", "blocked", error_code=blocked, error_message="Test delivery configuration or payment validation blocked this action.")
        else:
            provider = email_provider if kind == "email" else refund_provider
            if provider is None:
                provider = ResendAdapter() if kind == "email" else StripeAdapter()
                owned.append(provider)
            if kind == "email":
                if reconciling and provider_id:
                    result = await provider.retrieve(provider_id)
                elif store.now() >= deadline:
                    result = ProviderResult("unknown", "unknown", error_code="idempotency_expired", error_message="The safe retry window expired. Do not resend; investigate provider evidence.")
                elif not _reserve_send(operation_id, token):
                    result = ProviderResult("unknown", "unknown", error_code="send_attempt_limit", error_message="The send attempt limit is reached. Read-only reconciliation remains available.")
                else:
                    result = await provider.send(EmailRequest(**request), key)
            else:
                if reconciling and provider_id:
                    # The adapter verifies the account and source charge. A config change
                    # cannot revoke the fact that a refund already succeeded.
                    result = await provider.retrieve(provider_id)
                else:
                    result = await _refund_submission(provider, request, operation_id, token, key, deadline, reconciling)
    except ProviderError as exc:
        result = exc.result
    except ValueError:
        result = ProviderResult("blocked", "blocked", error_code="invalid_frozen_request", error_message="The frozen request is invalid. No new intent is permitted.")
    except Exception:
        # A network call may have succeeded. Never fabricate a definite failure.
        log.warning("Provider operation %s has an unclassified result", operation_id)
        result = ProviderResult("unknown", "unknown", error_code="unclassified_provider_error", error_message="Provider execution is uncertain. Reconcile this operation.")
    finally:
        for provider in owned:
            await provider.aclose()
    with Session(engine) as session:
        op = session.get(DeliveryOperation, operation_id)
        if not op or op.claim_token != token or op.state != "executing":
            return
        result_id = result.provider_id or op.provider_id
        # Success needs provider identity, not merely a status string.
        if result.state in ("accepted", "succeeded") and not result_id:
            result = ProviderResult("unknown", "unknown", error_code="missing_provider_id", error_message="Provider success has no object identity.")
        op.state, op.provider_status, op.provider_id = result.state, result.provider_status, result_id
        if previous_state == "succeeded" and result.state not in ("succeeded", "pending", "failed"):
            op.state, op.provider_status = "succeeded", previous_provider_status
            op.provider_id = provider_id
        op.error_code, op.error_message = result.error_code, result.error_message
        op.updated_at, op.claim_token, op.claim_expires_at = store.now(), None, None
        op.version += 1
        # Reconciliation is bounded and never immediate spin-polling.
        op.next_check_at = store.now() + dt.timedelta(seconds=min(300, 5 * 2 ** min(first_attempt, 6)))
        session.add(op)
        attempt = session.get(OperationAttempt, attempt_id)
        attempt.ended_at, attempt.result = store.now(), result.state
        attempt.provider_id, attempt.provider_request_id, attempt.error_code = result_id, result.request_id, result.error_code
        session.add(attempt)
        session.flush()
        ticket = session.get(Ticket, ticket_id)
        _refresh_ticket(session, ticket)
        record = TicketEvent(ticket_id=ticket_id, run_id=run_id, kind="operation", timestamp=store.now(),
                             data={"operation": serialize_operation(op, session), "version": ticket.version, "run_id": run_id})
        session.add(record)
        session.commit()
        status, resolution = ticket.status, ticket.resolution
        event_id, version = record.id, ticket.version
        event_data = {"run_id": run_id, "kind": "operation", "timestamp": record.timestamp.isoformat(), "data": record.data}
    bus.publish_ticket_event(ticket_id, event_id, event_data)
    bus.publish_status(ticket_id, status, resolution, run_id=run_id, version=version)
    # Slack notification is best-effort and cannot repeat provider execution.
    try:
        from integrations import slack
        with Session(engine) as session:
            run = session.get(TicketRun, run_id)
            target = run.slack_target if run else None
        if target and hasattr(slack, "refresh_delivery"):
            await slack.refresh_delivery(ticket_id, target)
    except Exception:
        log.warning("Slack delivery refresh failed for ticket %s", ticket_id)


def recover_operations() -> None:
    with Session(engine) as session:
        for op in session.exec(select(DeliveryOperation).where(DeliveryOperation.state == "executing")).all():
            # Startup is single-process. Every old executing claim belongs to a dead process.
            op.state, op.claim_token, op.claim_expires_at = "unknown", None, None
            op.next_check_at, op.updated_at = store.now(), store.now()
            op.version += 1
            session.add(op)
        session.commit()


async def worker(stop: asyncio.Event) -> None:
    """A bounded single-process durable work pump, not a distributed queue."""
    while not stop.is_set():
        with Session(engine) as session:
            timestamp = store.now()
            # A crashed sender becomes unknown only after its claim expires.
            session.exec(update(DeliveryOperation).where(
                DeliveryOperation.state == "executing", DeliveryOperation.claim_expires_at < timestamp,
            ).values(state="unknown", claim_token=None, claim_expires_at=None, next_check_at=timestamp,
                     error_code="expired_claim", error_message="An interrupted provider call needs reconciliation."))
            session.commit()
            ids = session.exec(select(DeliveryOperation.id).where(
                DeliveryOperation.state.in_(["queued", "pending", "unknown", "succeeded"]),
                (DeliveryOperation.check_attempt_count < MAX_SCHEDULED_CHECKS) | DeliveryOperation.manual_check_requested,
                (DeliveryOperation.dependency_id.is_(None)) | DeliveryOperation.dependency_id.in_(
                    select(DeliveryOperation.id).where(DeliveryOperation.state == "succeeded")
                ),
                (DeliveryOperation.next_check_at.is_(None)) | (DeliveryOperation.next_check_at <= timestamp),
            ).order_by(DeliveryOperation.created_at).limit(10)).all()
        for identifier in ids:
            if stop.is_set():
                break
            try:
                await process_operation(identifier)
            except Exception:
                log.exception("Durable operation %s needs recovery", identifier)
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass
