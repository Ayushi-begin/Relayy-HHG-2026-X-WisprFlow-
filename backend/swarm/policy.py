"""Hard guardrails on the action agent's proposal, enforced in Python.

The model's proposal is advisory. These rules decide the final action: any rule that
fires forces the ticket to "escalate", and the rule ids are recorded on the result.
"""

import datetime as dt
from collections.abc import Callable

from sqlmodel import Session

from db import engine
from models import Payment, PaymentStatus
from swarm.state import ActionProposal, ActionResult, TicketState

AUTO_REFUND_LIMIT = 50.00
MIN_CONFIDENCE = 0.75

RULES = {
    "no_proposal": "The action model produced no usable proposal.",
    "missing_triage": "Triage did not complete.",
    "suspicious_ticket": "Triage flagged the ticket as suspicious.",
    "critical_urgency": "Triage rated the ticket critical.",
    "vip_angry": "VIP customer with angry sentiment.",
    "low_confidence": f"Model confidence below {MIN_CONFIDENCE:.2f}.",
    "refund_over_limit": f"Auto-refunds are limited to ${AUTO_REFUND_LIMIT:.2f}.",
    "no_matching_payment": "No successful payment from this customer matches the refund amount.",
    "missing_payment_id": "Auto-refunds must select one exact payment from scoped research.",
    "payment_not_in_research": "The selected payment was not returned by scoped research.",
    "invalid_refund_money": "Refund must be the exact positive full USD payment in integer cents.",
    "unverified_payment_binding": "The selected payment has no verified provider binding.",
    "ineligible_payment": "The selected payment is not deterministically eligible for a refund.",
    "unverified_provenance": "Automatic actions require successful real-model triage, research, and action stages.",
}

PaymentLookup = Callable[[int, int], Payment | None]


def owned_payment(payment_id: int, customer_id: int) -> Payment | None:
    """Load a payment only when it is owned by the ticket customer."""
    with Session(engine) as session:
        payment = session.get(Payment, payment_id)
        if payment is None or payment.customer_id != customer_id:
            return None
        return payment


def _as_utc(value: object) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed.astimezone(dt.timezone.utc)


def eligibility_error(payment: Payment, ticket_received_at: dt.datetime) -> str | None:
    """Return a stable reason when trusted fixture eligibility rejects this payment.

    This intentionally consumes only server-managed ``Payment.eligibility_evidence``.
    The model's narrative and free-form duplicate claims never establish eligibility.
    Fulfillment calls this same helper before it contacts Stripe.
    """
    evidence = payment.eligibility_evidence
    if not isinstance(evidence, dict):
        return "missing_eligibility_evidence"

    purchase_type = evidence.get("purchase_type")
    charge_at = _as_utc(evidence.get("charge_at"))
    verified_at = _as_utc(evidence.get("verified_at"))
    received_at = _as_utc(ticket_received_at)
    required_identity = ("workspace_id", "billing_period", "product_id", "eligibility_reason")
    if (
        purchase_type not in {"monthly", "annual", "addon", "upgrade", "enterprise"}
        or charge_at is None
        or verified_at is None
        or received_at is None
        or any(not evidence.get(key) for key in required_identity)
        or verified_at > received_at
        or verified_at < charge_at
    ):
        return "unverified_or_conflicting_eligibility"

    valid_until = _as_utc(evidence.get("valid_until")) if evidence.get("valid_until") else None
    if valid_until is not None and received_at > valid_until:
        return "stale_eligibility_evidence"
    if purchase_type == "enterprise":
        return "enterprise_requires_escalation"
    if purchase_type == "monthly":
        return "monthly_purchase_not_refundable"

    duplicate = evidence.get("eligibility_reason") in {"confirmed_duplicate", "duplicate"}
    if duplicate:
        # Fixture setup confirms the peer relation; equal amounts/descriptions alone are never enough.
        peer_id = evidence.get("duplicate_peer_payment_id")
        if not (
            isinstance(peer_id, int)
            and peer_id > 0
            and peer_id != payment.id
            and evidence.get("duplicate_peer_settled") is True
            and evidence.get("duplicate_peer_same_workspace_period_product") is True
            and evidence.get("duplicate_newer_charge") is True
        ):
            return "unverified_duplicate_evidence"
        return None

    if purchase_type not in {"annual", "addon", "upgrade"}:
        return "unsupported_purchase_type"
    if received_at < charge_at or received_at - charge_at > dt.timedelta(days=14):
        return "refund_window_expired"
    return None


def _has_real_model_provenance(state: TicketState) -> bool:
    return bool(state.provenance) and all(
        state.provenance.get(stage) == "real_model" for stage in ("triage", "research", "action")
    )


def rules_fired(
    state: TicketState,
    proposal: ActionProposal | None,
    payment_lookup: PaymentLookup = owned_payment,
) -> list[str]:
    fired = []
    triage, research = state.triage, state.research

    if proposal is None:
        fired.append("no_proposal")
    if triage is None:
        fired.append("missing_triage")
    else:
        if triage.is_suspicious:
            fired.append("suspicious_ticket")
        if triage.urgency == "critical":
            fired.append("critical_urgency")
        if triage.sentiment == "angry" and research and research.customer and research.customer.tier == "vip":
            fired.append("vip_angry")

    confidences = [c.confidence for c in (triage, proposal) if c is not None]
    if any(c < MIN_CONFIDENCE for c in confidences):
        fired.append("low_confidence")

    if proposal and proposal.decision == "auto_refund":
        payment = None
        if proposal.payment_id is None:
            fired.append("missing_payment_id")
        elif state.ticket.customer_id is None:
            fired.append("no_matching_payment")
        elif proposal.payment_id not in {item.id for item in (research.payments if research else [])}:
            fired.append("payment_not_in_research")
        else:
            payment = payment_lookup(proposal.payment_id, state.ticket.customer_id)
            if payment is None or payment.status != PaymentStatus.succeeded:
                fired.append("no_matching_payment")

        amount_minor = proposal.refund_amount_minor
        if (
            not isinstance(amount_minor, int)
            or isinstance(amount_minor, bool)
            or amount_minor <= 0
            or payment is None
            or payment.currency != "usd"
            or payment.amount_minor is None
            or amount_minor != payment.amount_minor
        ):
            fired.append("invalid_refund_money")
        if isinstance(amount_minor, int) and not isinstance(amount_minor, bool) and amount_minor > int(AUTO_REFUND_LIMIT * 100):
            fired.append("refund_over_limit")
        if payment is not None:
            if not (
                payment.provider_account_id
                and payment.stripe_charge_id
                and payment.binding_verified_at
                and payment.livemode is False
            ):
                fired.append("unverified_payment_binding")
            if error := eligibility_error(payment, state.ticket.created_at):
                fired.append("ineligible_payment")
                fired.append(f"eligibility_{error}")
    if proposal and proposal.decision != "escalate" and state.provenance and not _has_real_model_provenance(state):
        fired.append("unverified_provenance")
    return fired


def enforce(
    state: TicketState,
    proposal: ActionProposal | None,
    payment_lookup: PaymentLookup = owned_payment,
) -> ActionResult:
    fired = rules_fired(state, proposal, payment_lookup)

    if proposal is None:
        return ActionResult(
            decision="escalate",
            reply_draft="Thanks for reaching out. A member of our support team will review this and follow up shortly.",
            reasoning=RULES["no_proposal"],
            confidence=0.0,
            rules_fired=fired,
        )

    if not fired:
        return ActionResult(
            decision=proposal.decision,
            reply_draft=proposal.reply_draft,
            refund_amount=proposal.refund_amount if proposal.decision == "auto_refund" else None,
            payment_id=proposal.payment_id if proposal.decision == "auto_refund" else None,
            refund_amount_minor=proposal.refund_amount_minor if proposal.decision == "auto_refund" else None,
            reasoning=proposal.reasoning,
            confidence=proposal.confidence,
            proposal=proposal,
        )

    policy_note = "Policy: " + " ".join(RULES.get(r, r.replace("_", " ")) for r in fired)
    return ActionResult(
        decision="escalate",
        # The model's own escalate reply is fine; otherwise use its reply that promises no outcome.
        reply_draft=proposal.reply_draft if proposal.decision == "escalate" else proposal.holding_reply,
        reasoning=f"{proposal.reasoning}\n\n{policy_note}",
        confidence=proposal.confidence,
        proposal=proposal,
        rules_fired=fired,
    )
