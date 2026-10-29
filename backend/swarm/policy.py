"""Hard guardrails on the action agent's proposal, enforced in Python.

The model's proposal is advisory. These rules decide the final action: any rule that
fires forces the ticket to "escalate", and the rule ids are recorded on the result.
"""

from collections.abc import Callable

from sqlmodel import Session, select

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
}

# (customer_id, amount) -> True if a refundable payment exists
PaymentMatcher = Callable[[int, float], bool]


def has_matching_payment(customer_id: int, amount: float) -> bool:
    with Session(engine) as session:
        payments = session.exec(
            select(Payment).where(Payment.customer_id == customer_id, Payment.status == PaymentStatus.succeeded)
        ).all()
    return any(abs(p.amount - amount) < 0.005 for p in payments)


def rules_fired(state: TicketState, proposal: ActionProposal | None, matcher: PaymentMatcher) -> list[str]:
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
        amount = proposal.refund_amount
        if amount is None or amount <= 0 or amount > AUTO_REFUND_LIMIT:
            fired.append("refund_over_limit")
        if amount is None or state.ticket.customer_id is None or not matcher(state.ticket.customer_id, amount):
            fired.append("no_matching_payment")
    return fired


def enforce(
    state: TicketState, proposal: ActionProposal | None, matcher: PaymentMatcher = has_matching_payment
) -> ActionResult:
    fired = rules_fired(state, proposal, matcher)

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
            reasoning=proposal.reasoning,
            confidence=proposal.confidence,
            proposal=proposal,
        )

    policy_note = "Policy: " + " ".join(RULES[r] for r in fired)
    return ActionResult(
        decision="escalate",
        # The model's own escalate reply is fine; otherwise use its reply that promises no outcome.
        reply_draft=proposal.reply_draft if proposal.decision == "escalate" else proposal.holding_reply,
        reasoning=f"{proposal.reasoning}\n\n{policy_note}",
        confidence=proposal.confidence,
        proposal=proposal,
        rules_fired=fired,
    )
