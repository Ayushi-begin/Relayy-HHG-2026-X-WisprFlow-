import asyncio
import datetime as dt

from swarm import llm, tools
from swarm.action import _brief as action_brief
from swarm.research import _brief as research_brief
from swarm.state import ResearchResult, TicketData, TicketState, TriageResult
from swarm.triage import triage


def ticket(text: str) -> TicketData:
    return TicketData(id=1, customer_id=1, subject=text, body=text, created_at=dt.datetime.now(dt.timezone.utc))


def test_model_boundary_redacts_selected_card_and_phone_patterns():
    secret = "Call +1 (415) 555-1212; card 4111 1111 1111 1111"
    state = TicketState(
        ticket=ticket(secret),
        triage=TriageResult(
            category="billing", urgency="medium", sentiment="neutral", language="en", is_suspicious=False,
            confidence=0.9, summary=secret,
        ),
        research=ResearchResult(key_findings=[secret]),
    )
    for prompt in (llm.ticket_block(state.ticket), research_brief(state), action_brief(state)):
        assert "4111 1111 1111 1111" not in prompt
        assert "415) 555-1212" not in prompt
        assert "[REDACTED CARD]" in prompt
        assert "[REDACTED PHONE]" in prompt


def test_tool_scope_missing_fails_closed():
    ctx = tools.ToolContext(allowed_customer_id=None)
    output, is_error = tools.execute(ctx, "get_payments", {"customer_id": 1, "days": 30})
    assert is_error is True
    assert "scope is required" in output
    output, is_error = tools.execute(ctx, "lookup_customer", {"email_or_id": "someone@example.test"})
    assert is_error is True
    assert "scope is required" in output


def test_tool_text_is_sanitized_before_returning_to_model(monkeypatch):
    ctx = tools.ToolContext(allowed_customer_id=1)

    def handler(_ctx, value):
        return {"text": value}

    monkeypatch.setitem(tools._HANDLERS, "test_text", handler)
    output, is_error = tools.execute(ctx, "test_text", {"value": "4111 1111 1111 1111"})
    assert is_error is False
    assert "4111 1111 1111 1111" not in output
    assert "[REDACTED CARD]" in output


def test_missing_model_configuration_records_simulated_provenance(monkeypatch):
    import swarm.triage as triage_module

    monkeypatch.setattr(triage_module.config, "ANTHROPIC_API_KEY", "")
    provenance, errors = {}, {}
    result = asyncio.run(triage(ticket("Need help"), provenance=provenance, provenance_errors=errors))
    assert result.confidence > 0
    assert provenance == {"triage": "simulated"}
    assert "configured model id unavailable" in errors["triage"]


def test_unknown_model_pricing_is_unavailable():
    class Response:
        class usage:
            input_tokens = 2
            output_tokens = 3
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

    usage = []
    llm.record_usage(usage, "triage", "unverified-model", Response())
    assert usage[0].cost_usd is None
