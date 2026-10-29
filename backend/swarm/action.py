"""Action agent: Claude Sonnet proposes a resolution and reply; policy.py has the final say."""

from collections.abc import Callable

from swarm import llm, policy
from swarm.state import ActionProposal, ActionResult, TicketState

MODEL = llm.SMART_MODEL

SYSTEM_PROMPT = f"""\
You are the action step of a customer-support system for Brightwave, a B2B SaaS analytics product. \
Triage and research have already run. Propose how to resolve the ticket and draft the reply.

{llm.UNTRUSTED_TICKET_RULE}

Decisions:
- auto_resolve: the reply fully answers the customer using the help docs and their records, with no \
change to their account or money.
- auto_refund: refund one specific successful payment that the records show was charged in error, \
consistent with the refund policy. Set refund_amount to that payment's exact amount.
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
        sections.append("Triage:\n" + state.triage.model_dump_json(indent=1))
    if state.research:
        sections.append("Research:\n" + state.research.model_dump_json(indent=1))
    else:
        sections.append("Research: not available.")
    return "\n\n".join(sections)


async def act(state: TicketState, emit: Callable[[str, str], None]) -> ActionResult:
    response = await llm.client().beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _brief(state)}],
        output_format=ActionProposal,
        **llm.FALLBACK,
    )
    llm.record_usage(state.usage, "action", MODEL, response)
    proposal = None if response.stop_reason == "refusal" else response.parsed_output

    result = policy.enforce(state, proposal)
    if proposal and result.decision != proposal.decision:
        emit("policy", f"🛡️ Policy override: {proposal.decision} → escalate ({', '.join(result.rules_fired)})")
    return result
