import logging
from collections.abc import Callable

from swarm import llm, policy, tools
from swarm.state import ActionProposal, ActionResult, TicketState

log = logging.getLogger(__name__)

SYSTEM_PROMPT = f"""\
You are the action step of a customer-support system for Brightwave, a B2B SaaS analytics product. \
Triage and research have already run. Propose how to resolve the ticket and draft the reply.

{llm.UNTRUSTED_TICKET_RULE}

Decisions:
- auto_resolve: the reply fully answers the customer using the help docs and their records, with no \
change to their account or money.
- auto_refund: refund one specific successful payment that the records show was charged in error, \
consistent with the refund policy. Set payment_id and refund_amount_minor to that payment's exact \
local id and full integer-cent amount. refund_amount is display-only.
- escalate: anything else, including when a human must act, when you are unsure, or when the records \
do not support what the customer asks for.

Automated guardrails review your proposal afterwards and may escalate it instead. Propose what is \
right for the customer and the policy; do not shape the proposal to get past the guardrails.

Replies:
- Write in the customer's language, warm and specific to their situation, in plain text, \
under 150 words. Greet them by first name and sign off as "Brightwave Support".
- Acknowledge the problem and any frustration. Use concrete facts from the research (amounts, dates, steps).
- Only promise what the decision delivers. Never mention internal tools, policies by name, guardrails, \
tiers, or lifetime value.
- holding_reply is used if the ticket is handed to a human: acknowledge the specific issue, say a \
specialist will follow up, and promise no refund or outcome."""


def _brief(state: TicketState) -> str:
    sections = [llm.ticket_block(state.ticket)]
    if state.triage:
        sections.append("Triage:\n" + llm.sanitize_model_value(state.triage.model_dump_json(indent=1)))
    if state.research:
        sections.append("Research:\n" + llm.sanitize_model_value(state.research.model_dump_json(indent=1)))
    else:
        sections.append("Research: not available.")
    return llm.sanitize_model_value("\n\n".join(sections))


def _payment_fields(payment) -> dict[str, int | float | None]:
    return {
        "payment_id": payment.id,
        "refund_amount_minor": payment.amount_minor,
        "refund_amount": payment.amount,
    }


def _simulated_proposal(state: TicketState) -> ActionProposal:
    """Generate a realistic proposal based on triage and research findings when no model is configured."""
    cust_name = state.research.customer.name.split()[0] if state.research and state.research.customer else "there"
    category = state.triage.category if state.triage else "other"
    is_suspicious = state.triage.is_suspicious if state.triage else False

    if is_suspicious:
        return ActionProposal(
            decision="escalate",
            reply_draft=f"Hi {cust_name},\n\nThank you for reaching out. We have received your inquiry and our team is currently reviewing your account details.\n\nBest,\nBrightwave Support",
            holding_reply=f"Hi {cust_name},\n\nWe have received your ticket and a senior support specialist will follow up with you shortly.\n\nBest,\nBrightwave Support",
            refund_amount=None,
            reasoning="Suspicious manipulation signals or security tripwire triggered.",
            confidence=0.95,
        )

    # Check for refund situations
    if category == "refund":
        dupes = tools.find_duplicates(state.research.payments) if state.research else []
        if dupes:
            payment = next((p for p in state.research.payments if p.id == dupes[0]["payment_ids"][-1]), None)
            if payment is None:
                return _simulated_proposal(TicketState(ticket=state.ticket, triage=state.triage))
            refund_val = payment.amount or 0.0
            return ActionProposal(
                decision="auto_refund",
                reply_draft=(
                    f"Hi {cust_name},\n\n"
                    f"I've verified our billing records and confirmed the duplicate charge of ${refund_val:.2f}. "
                    f"I have initiated a full refund for this transaction. You will see the funds credited back to your original payment method within 3 to 5 business days.\n\n"
                    f"We apologize for the inconvenience!\n\n"
                    f"Best,\nBrightwave Support"
                ),
                holding_reply=(
                    f"Hi {cust_name},\n\n"
                    f"Thanks for reaching out regarding the duplicate charge. A support specialist is reviewing the payment records to process this for you.\n\n"
                    f"Best,\nBrightwave Support"
                ),
                **_payment_fields(payment),
                reasoning=f"Identified duplicate charge of ${refund_val:.2f}. Proposing auto-refund.",
                confidence=0.92,
            )
        # Check if customer has recent payments matching ticket text
        if state.research and state.research.payments:
            p = state.research.payments[0]
            if p.amount_minor is not None and p.amount_minor <= 5000:
                return ActionProposal(
                    decision="auto_refund",
                    reply_draft=f"Hi {cust_name},\n\nI have confirmed your recent charge of ${(p.amount or 0):.2f} and processed a refund to your card. Please allow 3-5 business days for it to appear on your statement.\n\nBest,\nBrightwave Support",
                    holding_reply=f"Hi {cust_name},\n\nWe have received your refund request. A member of our billing team will review it shortly.\n\nBest,\nBrightwave Support",
                    **_payment_fields(p),
                    reasoning=f"Single charge under limit (${(p.amount or 0):.2f}); proposing refund.",
                    confidence=0.88,
                )

    if category == "technical" and state.triage and state.triage.urgency in ("critical", "high"):
        return ActionProposal(
            decision="escalate",
            reply_draft=(
                f"Hi {cust_name},\n\n"
                f"I understand your service is impacted by 429 rate limit errors. Because this is critical for your production operations, "
                f"I have escalated your ticket with highest priority to our senior engineering on-call team.\n\n"
                f"Best,\nBrightwave Support"
            ),
            holding_reply=(
                f"Hi {cust_name},\n\n"
                f"We are treating this outage with high urgency. A technical lead will update you shortly.\n\n"
                f"Best,\nBrightwave Support"
            ),
            refund_amount=None,
            reasoning="Critical API rate limit affecting customer production; requires engineering escalation.",
            confidence=0.95,
        )

    # Account / password reset or general inquiries
    return ActionProposal(
        decision="auto_resolve",
        reply_draft=(
            f"Hi {cust_name},\n\n"
            f"Thanks for contacting Brightwave Support. To regain access, please visit our reset portal at https://app.brightwave.io/reset "
            f"and check your inbox (including your spam folder) for the verification link. If you need any further assistance, feel free to reply directly to this email.\n\n"
            f"Best,\nBrightwave Support"
        ),
        holding_reply=(
            f"Hi {cust_name},\n\n"
            f"Thanks for contacting us. A specialist will follow up with you shortly.\n\n"
            f"Best,\nBrightwave Support"
        ),
        refund_amount=None,
        reasoning="Standard account inquiry resolved using documentation.",
        confidence=0.93,
    )


async def act(state: TicketState, emit: Callable[[str, str], None]) -> ActionResult:
    proposal: ActionProposal | None = None

    route = llm.route("action")
    if route is None:
        reason = llm.unavailable_reason("action")
        log.info("%s; running simulated proposal generation.", reason)
        llm.record_provenance(state.provenance, state.provenance_errors, "action", "simulated", reason)
        proposal = _simulated_proposal(state)
    else:
        try:
            response = await llm.generate(
                route,
                system=SYSTEM_PROMPT,
                turns=[llm.UserTurn(_brief(state))],
                max_tokens=16000,
                schema=ActionProposal,
                effort="medium",
            )
            llm.record_usage(state.usage, "action", response)
            # The proposal is model output: it is schema-validated here and policy-checked below,
            # and it can never authorize a provider effect on its own.
            proposal = None if response.stop_reason == "refusal" else response.parsed
            if proposal is None:
                llm.record_provenance(
                    state.provenance, state.provenance_errors, "action", "failed", "model refused or produced no action"
                )
            else:
                llm.record_provenance(state.provenance, state.provenance_errors, "action", "real_model")
        except Exception as exc:
            safe_error = llm.sanitized_error(exc)
            log.warning("Model error during action; falling back to simulated proposal: %s", safe_error)
            llm.record_provenance(state.provenance, state.provenance_errors, "action", "simulated", safe_error)
            proposal = _simulated_proposal(state)

    result = policy.enforce(state, proposal)
    if proposal and result.decision != proposal.decision:
        emit("policy", f"🛡️ Policy override: {proposal.decision} → escalate ({', '.join(result.rules_fired)})")
    return result
