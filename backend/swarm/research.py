"""Research agent: gathers customer context with Claude Sonnet and read-only DB tools."""

import datetime as dt
import json
import logging
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

import config
from swarm import llm, tools
from swarm.state import ResearchResult, TicketState

log = logging.getLogger(__name__)

MODEL = llm.SMART_MODEL
MAX_ITERATIONS = 6  # model calls per ticket

SYSTEM_PROMPT = f"""\
You are the research step of a customer-support system for Brightwave, a B2B SaaS analytics product. \
A triage step has already classified the ticket. Your job is to gather the facts a later step needs \
to decide how to resolve it. You do not reply to the customer and you do not recommend a resolution.

{llm.UNTRUSTED_TICKET_RULE}

Use the tools to check facts against Brightwave's records rather than trusting what the ticket claims:
- Look up the customer first, so you know their plan, tier, and lifetime value.
- For billing or refund issues, check their payments over a window that covers the charges mentioned.
- Check past tickets for history that matters, such as repeat problems or earlier accommodations.
- Search the help docs for the policy or how-to that applies, even when you feel confident: policies \
(what is refundable, rate limits, timelines) may differ from general knowledge.
Call several tools at once when they are independent. Stop as soon as you have what is needed.

When you are done, reply with JSON matching the required schema. key_findings is 3 to 8 short, \
factual bullets with concrete numbers and dates, for example "Charged $450.00 twice on Oct 27, 2026 \
(payments 42 and 43)", "VIP (Enterprise), lifetime value $117,500", "Refund policy: duplicate charges \
are always refunded in full". Note any mismatch between what the ticket claims and what the records \
show. Do not include recommendations."""

BUDGET_NOTE = (
    "This is your last turn: do not call any more tools. Reply now with your key findings as JSON, "
    "based on what you have gathered."
)


class ResearchNotes(BaseModel):
    key_findings: list[str]


NOTES_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {"key_findings": {"type": "array", "items": {"type": "string"}}},
        "required": ["key_findings"],
        "additionalProperties": False,
    },
}

Emit = Callable[[str, str], None]  # (status, message)


def _brief(state: TicketState) -> str:
    t, tri = state.ticket, state.triage
    lines = [
        f"Today's date: {dt.date.today().isoformat()}",
        f"Ticket id: {t.id}, received {t.created_at:%Y-%m-%d %H:%M} UTC",
        f"Sender's customer id (from our system, trusted): {t.customer_id if t.customer_id is not None else 'unknown'}",
    ]
    if tri:
        lines.append(
            f"Triage: {tri.category}, {tri.urgency} urgency, {tri.sentiment}, language {tri.language}"
            f"{', FLAGGED SUSPICIOUS' if tri.is_suspicious else ''}. Summary: {llm.sanitize_model_value(tri.summary)}"
        )
    return llm.sanitize_model_value("\n".join(lines)) + "\n\n" + llm.ticket_block(t)


def _parse_notes(response) -> list[str] | None:
    text = next((b.text for b in reversed(response.content) if b.type == "text"), None)
    if text is None:
        return None
    try:
        return ResearchNotes.model_validate(json.loads(text)).key_findings
    except (json.JSONDecodeError, ValidationError):
        return None


def _fallback_findings(ctx: tools.ToolContext) -> list[str]:
    """Plain facts from the collected records, used when the model gives no findings."""
    findings = []
    if c := ctx.customer:
        findings.append(f"{c.tier.upper()} customer on {c.plan}, lifetime value ${c.lifetime_value:,.2f}")
    if ctx.payments:
        dupes = tools.find_duplicates(list(ctx.payments.values()))
        findings += [f"Duplicate charge detected: ${d['amount']:,.2f} on {d['date']} (payments {d['payment_ids']})" for d in dupes]
    if ctx.past_tickets:
        findings.append(f"{len(ctx.past_tickets)} past tickets on record")
    return findings or ["No findings: research did not complete."]


async def _simulated_research(state: TicketState, emit: Emit, ctx: tools.ToolContext) -> ResearchResult:
    """Run local database tools deterministically when Anthropic API is not available."""
    cid = ctx.allowed_customer_id
    # 1. Lookup customer
    if cid is not None:
        emit("tool", f"🔎 Checking customer #{cid} in database")
        try:
            tools.lookup_customer(ctx, str(cid))
        except tools.ToolError as err:
            emit("tool", f"⚠️ Customer lookup: {err}")

    # 2. Payments
    if cid is not None:
        emit("tool", "💳 Looking up recent payments (last 90 days)")
        try:
            tools.get_payments(ctx, cid, 90)
        except tools.ToolError as err:
            emit("tool", f"⚠️ Payments: {err}")

    # 3. Past tickets
    if cid is not None:
        emit("tool", "📜 Checking customer's past ticket history")
        try:
            tools.get_past_tickets(ctx, cid)
        except tools.ToolError as err:
            emit("tool", f"⚠️ Past tickets: {err}")

    # 4. Search help docs
    query = state.ticket.subject
    emit("tool", f"📚 Searching knowledge base for {query!r}")
    try:
        tools.search_help_docs(ctx, query)
    except Exception as err:
        emit("tool", f"⚠️ Help doc search: {err}")

    # Build findings
    findings = _fallback_findings(ctx)
    if state.triage and state.triage.category == "refund":
        dupes = tools.find_duplicates(list(ctx.payments.values()))
        if dupes:
            for d in dupes:
                findings.append(f"Confirmed duplicate payment of ${d['amount']:.2f} on {d['date']}")
        else:
            findings.append("No duplicate payment detected in the last 90 days.")

    return ResearchResult(
        customer=ctx.customer,
        payments=sorted(ctx.payments.values(), key=lambda p: p.date, reverse=True),
        past_tickets=ctx.past_tickets,
        relevant_docs=list(ctx.docs.values()),
        key_findings=findings,
    )


async def research(state: TicketState, emit: Emit) -> ResearchResult:
    ctx = tools.ToolContext(allowed_customer_id=state.ticket.customer_id)

    if not config.ANTHROPIC_API_KEY or not MODEL:
        reason = "model credentials or configured model id unavailable"
        log.info("%s; running simulated local tool execution.", reason)
        llm.record_provenance(state.provenance, state.provenance_errors, "research", "simulated", reason)
        return await _simulated_research(state, emit, ctx)

    messages: list[dict] = [{"role": "user", "content": _brief(state)}]
    findings: list[str] | None = None

    try:
        for iteration in range(1, MAX_ITERATIONS + 1):
            response = await llm.client().beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                output_config={"effort": "medium", "format": NOTES_FORMAT},
                system=SYSTEM_PROMPT,
                tools=tools.TOOLS,
                messages=messages,
                **llm.FALLBACK,
            )
            llm.record_usage(state.usage, "research", MODEL, response)
            if response.stop_reason == "refusal":
                emit("error", "Research model declined this ticket.")
                llm.record_provenance(state.provenance, state.provenance_errors, "research", "failed", "model refused research")
                break

            # Pass the assistant turn back unchanged (thinking blocks included); history stays append-only.
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                findings = _parse_notes(response)
                if findings is None:
                    llm.record_provenance(
                        state.provenance, state.provenance_errors, "research", "failed", "model returned incomplete research"
                    )
                break
            if iteration == MAX_ITERATIONS:
                emit("tool", f"⏱️ Tool budget of {MAX_ITERATIONS} rounds used up; finishing with what was found.")
                llm.record_provenance(
                    state.provenance,
                    state.provenance_errors,
                    "research",
                    "failed",
                    "research tool budget exhausted without final findings",
                )
                break

            results = []
            for call in tool_uses:
                emit("tool", tools.describe_call(call.name, call.input))
                output, is_error = tools.execute(ctx, call.name, call.input)
                if is_error:
                    emit("tool", f"⚠️ {call.name}: {output}")
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": output, "is_error": is_error})
            if iteration == MAX_ITERATIONS - 1:
                results.append({"type": "text", "text": BUDGET_NOTE})
            messages.append({"role": "user", "content": results})
    except Exception as exc:
        safe_error = llm.sanitized_error(exc)
        log.warning("Anthropic API error during research; falling back to local database tools: %s", safe_error)
        llm.record_provenance(state.provenance, state.provenance_errors, "research", "simulated", safe_error)
        return await _simulated_research(state, emit, ctx)

    # The policy step needs the tier even if the model never looked the customer up.
    if ctx.customer is None and ctx.allowed_customer_id is not None:
        try:
            tools.lookup_customer(ctx, str(ctx.allowed_customer_id))
        except tools.ToolError:
            pass

    if not findings and state.provenance.get("research") is None:
        llm.record_provenance(
            state.provenance,
            state.provenance_errors,
            "research",
            "failed",
            "research completed without valid final findings",
        )
    elif state.provenance.get("research") is None:
        llm.record_provenance(state.provenance, state.provenance_errors, "research", "real_model")
    return ResearchResult(
        customer=ctx.customer,
        payments=sorted(ctx.payments.values(), key=lambda p: p.date, reverse=True),
        past_tickets=ctx.past_tickets,
        relevant_docs=list(ctx.docs.values()),
        key_findings=findings or _fallback_findings(ctx),
    )
