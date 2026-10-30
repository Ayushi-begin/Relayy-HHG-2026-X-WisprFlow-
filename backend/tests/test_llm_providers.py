"""Offline tests for the provider-neutral LLM layer: adapters, routing, retries and health."""

import asyncio
import datetime as dt
from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest
from google.genai import types

import config
from swarm import llm
from swarm.providers import health, router
from swarm.providers.anthropic_provider import AnthropicProvider
from swarm.providers.base import (
    AssistantTurn, LLMRequest, LLMResponse, ProviderError, TokenUsage, ToolCall, ToolResult, ToolResultsTurn,
    ToolSpec, UserTurn, parse_json,
)
from swarm.providers.gemini_provider import GeminiProvider
from swarm.providers.openai_provider import OpenAIProvider
from swarm.state import TicketData, TriageResult
from swarm.triage import triage

TRIAGE_JSON = (
    '{"category":"billing","urgency":"high","sentiment":"frustrated","language":"en",'
    '"is_suspicious":false,"confidence":0.9,"summary":"Duplicate charge"}'
)
TOOL = ToolSpec("lookup_customer", "Look up a customer", {"type": "object", "properties": {"id": {"type": "string"}}})


@pytest.fixture(autouse=True)
def clean_health():
    health.reset()
    yield
    health.reset()


def _request(**overrides) -> LLMRequest:
    values = {"model": "m", "system": "sys", "turns": (UserTurn("hello"),), "max_tokens": 100}
    return LLMRequest(**{**values, **overrides})


def _with_client(provider, client):
    provider.__dict__["_client"] = client  # replaces the lazily built SDK client
    return provider


# --- shared helpers ----------------------------------------------------------------------------


def test_parse_json_validates_and_strips_code_fences():
    assert parse_json(TriageResult, f"```json\n{TRIAGE_JSON}\n```").category == "billing"
    assert parse_json(TriageResult, '{"category":"billing"}') is None
    assert parse_json(TriageResult, "not json") is None


# --- routing -----------------------------------------------------------------------------------


@pytest.fixture
def models(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_MODELS", {
        "gemini": {"fast": "g-fast", "smart": "g-smart"},
        "anthropic": {"fast": "claude-haiku-5-5", "smart": "claude-opus-5-5"},
        "openai": {"fast": "", "smart": ""},
    })
    monkeypatch.setattr(config, "LLM_ROUTES", {"triage": "", "research": "", "action": ""})
    monkeypatch.setattr(config, "LLM_ROUTING", "static")
    monkeypatch.setattr(config, "LLM_FALLBACK_PROVIDER", "")


def test_static_routing_uses_tiers(models):
    assert router.resolve("triage", configured={"gemini"}).model == "g-fast"
    assert router.resolve("research", configured={"gemini"}).model == "g-smart"
    assert router.resolve("action", configured=set()) is None


def test_explicit_route_overrides_provider(models, monkeypatch):
    monkeypatch.setattr(config, "LLM_ROUTES", {"triage": "anthropic:claude-haiku-5-5", "research": "", "action": ""})
    route = router.resolve("triage", configured={"gemini", "anthropic"})
    assert (route.provider, route.model) == ("anthropic", "claude-haiku-5-5")
    assert router.resolve("triage", configured={"gemini"}) is None  # never silently substitutes


def test_adaptive_routing_escalates_complex_triage_only_when_enabled(models, monkeypatch):
    assert router.resolve("triage", complexity=1.0, configured={"gemini"}).model == "g-fast"
    monkeypatch.setattr(config, "LLM_ROUTING", "adaptive")
    route = router.resolve("triage", complexity=1.0, configured={"gemini"})
    assert route.model == "g-smart" and "escalated" in route.reason


def test_adaptive_fallback_requires_explicit_configuration(models, monkeypatch):
    monkeypatch.setattr(config, "LLM_ROUTING", "adaptive")
    for _ in range(health.FAILURE_THRESHOLD):
        health.record_failure("gemini", "http_503")
    assert health.circuit_open("gemini")
    assert router.resolve("research", configured={"gemini", "anthropic"}).provider == "gemini"
    monkeypatch.setattr(config, "LLM_FALLBACK_PROVIDER", "anthropic")
    route = router.resolve("research", configured={"gemini", "anthropic"})
    assert (route.provider, route.model) == ("anthropic", "claude-opus-5-5")
    assert route.reason == "fallback: gemini circuit open"


# --- retries, timeouts, health -----------------------------------------------------------------


class FlakyProvider:
    name = "gemini"

    def __init__(self, failures: list[Exception]):
        self.failures = failures
        self.calls = 0

    def configured(self):
        return True

    async def generate(self, request):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return LLMResponse("gemini", request.model, "ok", [], "end", TokenUsage(1, 1))


@pytest.fixture
def flaky(monkeypatch):
    def install(failures):
        provider = FlakyProvider(failures)
        monkeypatch.setattr(llm, "providers", lambda: {"gemini": provider})
        real_sleep = asyncio.sleep
        monkeypatch.setattr(llm.asyncio, "sleep", lambda _delay: real_sleep(0))
        return provider
    return install


ROUTE = router.Route("triage", "gemini", "g-fast", "fast", "test")


def test_transient_errors_are_retried_and_recorded(flaky, monkeypatch):
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 2)
    provider = flaky([ProviderError("gemini", "http_429", transient=True)])
    response = asyncio.run(llm.generate(ROUTE, system="s", turns=[UserTurn("x")], max_tokens=10))
    assert response.text == "ok" and provider.calls == 2
    stats = health.snapshot()["gemini"]
    assert (stats["failures"], stats["successes"], stats["status"]) == (1, 1, "ok")


def test_permanent_errors_are_not_retried(flaky):
    provider = flaky([ProviderError("gemini", "http_400", transient=False)])
    with pytest.raises(ProviderError):
        asyncio.run(llm.generate(ROUTE, system="s", turns=[UserTurn("x")], max_tokens=10))
    assert provider.calls == 1


def test_retries_are_bounded(flaky, monkeypatch):
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 1)
    provider = flaky([ProviderError("gemini", "http_503", transient=True)] * 5)
    with pytest.raises(ProviderError):
        asyncio.run(llm.generate(ROUTE, system="s", turns=[UserTurn("x")], max_tokens=10))
    assert provider.calls == 2


def test_timeouts_become_transient_provider_errors(monkeypatch):
    class Slow(FlakyProvider):
        async def generate(self, request):
            self.calls += 1
            await asyncio.sleep(1)

    provider = Slow([])
    monkeypatch.setattr(llm, "providers", lambda: {"gemini": provider})
    monkeypatch.setattr(config, "LLM_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 0)
    with pytest.raises(ProviderError) as info:
        asyncio.run(llm.generate(ROUTE, system="s", turns=[UserTurn("x")], max_tokens=10))
    assert info.value.kind == "timeout"
    assert health.snapshot()["gemini"]["last_error"] == "timeout"


# --- Anthropic adapter -------------------------------------------------------------------------


class FakeAnthropicMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def create(self, **params):
        self.calls.append(("create", params))
        if self.error:
            raise self.error
        return self.response

    async def parse(self, **params):
        self.calls.append(("parse", params))
        if self.error:
            raise self.error
        return self.response


def _anthropic(messages):
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return _with_client(AnthropicProvider("key", timeout=5), client)


def _claude_response(content, stop="end_turn", parsed=None, model="claude-opus-5-5"):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=2, cache_creation_input_tokens=1)
    return SimpleNamespace(content=content, stop_reason=stop, parsed_output=parsed, usage=usage, model=model)


def test_anthropic_structured_output_and_fallback_opt_in():
    parsed = TriageResult.model_validate_json(TRIAGE_JSON)
    messages = FakeAnthropicMessages(_claude_response([SimpleNamespace(type="text", text=TRIAGE_JSON)], parsed=parsed))
    response = asyncio.run(_anthropic(messages).generate(_request(model="claude-opus-5-5", schema=TriageResult, effort="low")))
    kind, params = messages.calls[0]
    assert kind == "parse" and params["output_format"] is TriageResult
    assert params["output_config"] == {"effort": "low"}
    assert params["fallbacks"] == "default" and params["betas"] == ["server-side-fallback-2026-07-01"]
    assert response.parsed is parsed and response.stop_reason == "end"
    assert response.usage == TokenUsage(10, 5, 2, 1)


def test_anthropic_omits_fallback_for_models_without_it():
    messages = FakeAnthropicMessages(_claude_response([SimpleNamespace(type="text", text="hi")], model="claude-haiku-5-5"))
    asyncio.run(_anthropic(messages).generate(_request(model="claude-haiku-5-5")))
    assert "fallbacks" not in messages.calls[0][1]


def test_anthropic_tool_loop_replays_native_content_and_results():
    tool_use = SimpleNamespace(type="tool_use", id="tu_1", name="lookup_customer", input={"id": "1"})
    messages = FakeAnthropicMessages(_claude_response([tool_use], stop="tool_use"))
    provider = _anthropic(messages)
    first = asyncio.run(provider.generate(_request(tools=(TOOL,))))
    assert first.stop_reason == "tool_use" and first.tool_calls == [ToolCall("tu_1", "lookup_customer", {"id": "1"})]

    turns = (UserTurn("hello"), AssistantTurn(first),
             ToolResultsTurn((ToolResult("tu_1", "lookup_customer", "{}", True),), note="last turn"))
    asyncio.run(provider.generate(_request(tools=(TOOL,), turns=turns)))
    sent = messages.calls[1][1]["messages"]
    assert sent[1] == {"role": "assistant", "content": [tool_use]}
    assert sent[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "{}", "is_error": True},
        {"type": "text", "text": "last turn"},
    ]


def test_anthropic_errors_are_classified():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    rate = anthropic.RateLimitError("slow down", response=httpx.Response(429, request=request), body=None)
    with pytest.raises(ProviderError) as info:
        asyncio.run(_anthropic(FakeAnthropicMessages(error=rate)).generate(_request()))
    assert (info.value.kind, info.value.transient) == ("http_429", True)

    bad = anthropic.BadRequestError("bad", response=httpx.Response(400, request=request), body=None)
    with pytest.raises(ProviderError) as info:
        asyncio.run(_anthropic(FakeAnthropicMessages(error=bad)).generate(_request()))
    assert info.value.transient is False


def test_assistant_turns_never_cross_providers():
    foreign = LLMResponse("gemini", "g", "", [], "end", None)
    with pytest.raises(ProviderError):
        asyncio.run(_anthropic(FakeAnthropicMessages()).generate(_request(turns=(UserTurn("x"), AssistantTurn(foreign)))))


# --- OpenAI adapter ----------------------------------------------------------------------------


class FakeCompletions:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def create(self, **params):
        self.calls.append(("create", params))
        if self.error:
            raise self.error
        return self.response

    async def parse(self, **params):
        self.calls.append(("parse", params))
        return self.response


def _openai(completions):
    return _with_client(OpenAIProvider("key", timeout=5), SimpleNamespace(chat=SimpleNamespace(completions=completions)))


def _completion(message, finish="stop"):
    usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3, prompt_tokens_details=SimpleNamespace(cached_tokens=4))
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=usage, model="gpt-test")


def test_openai_tool_calls_and_json_mode_for_tool_loops():
    call = SimpleNamespace(id="call_1", function=SimpleNamespace(name="lookup_customer", arguments='{"id": "1"}'))
    completions = FakeCompletions(_completion(SimpleNamespace(content=None, tool_calls=[call], refusal=None), "tool_calls"))
    provider = _openai(completions)
    first = asyncio.run(provider.generate(_request(tools=(TOOL,), schema=TriageResult)))
    kind, params = completions.calls[0]
    assert kind == "create" and params["response_format"] == {"type": "json_object"}
    assert params["messages"][0] == {"role": "system", "content": "sys"}
    assert first.tool_calls == [ToolCall("call_1", "lookup_customer", {"id": "1"})]
    assert first.usage == TokenUsage(7, 3, 4, 0)

    completions.response = _completion(SimpleNamespace(content=TRIAGE_JSON, tool_calls=None, refusal=None))
    turns = (UserTurn("hello"), AssistantTurn(first), ToolResultsTurn((ToolResult("call_1", "lookup_customer", "oops", True),)))
    final = asyncio.run(provider.generate(_request(tools=(TOOL,), schema=TriageResult, turns=turns)))
    sent = completions.calls[1][1]["messages"]
    assert sent[2]["tool_calls"][0]["id"] == "call_1"
    assert sent[3] == {"role": "tool", "tool_call_id": "call_1", "content": "ERROR: oops"}
    assert final.parsed.category == "billing"


def test_openai_refusal_and_structured_parse():
    completions = FakeCompletions(_completion(SimpleNamespace(content=None, tool_calls=None, refusal="no")))
    response = asyncio.run(_openai(completions).generate(_request(schema=TriageResult)))
    assert completions.calls[0][0] == "parse"
    assert response.stop_reason == "refusal" and response.parsed is None


def test_openai_errors_are_classified():
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    error = openai.InternalServerError("down", response=httpx.Response(500, request=request), body=None)
    with pytest.raises(ProviderError) as info:
        asyncio.run(_openai(FakeCompletions(error=error)).generate(_request()))
    assert (info.value.kind, info.value.transient) == ("http_500", True)


# --- Gemini adapter ----------------------------------------------------------------------------


class FakeGeminiModels:
    def __init__(self, response):
        self.response, self.calls = response, []

    async def generate_content(self, **params):
        self.calls.append(params)
        return self.response


def _gemini(models):
    return _with_client(GeminiProvider("key"), SimpleNamespace(aio=SimpleNamespace(models=models)))


def _gemini_response(parts, finish="STOP", calls=None, parsed=None):
    content = types.Content(role="model", parts=parts)
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=content, finish_reason=SimpleNamespace(name=finish))],
        function_calls=calls, parsed=parsed, prompt_feedback=None,
        usage_metadata=SimpleNamespace(prompt_token_count=5, candidates_token_count=2, thoughts_token_count=1,
                                       cached_content_token_count=0),
    )


def test_gemini_structured_output_without_tools():
    models = FakeGeminiModels(_gemini_response([types.Part.from_text(text=TRIAGE_JSON)]))
    response = asyncio.run(_gemini(models).generate(_request(schema=TriageResult)))
    config_sent = models.calls[0]["config"]
    assert config_sent.response_schema is TriageResult and config_sent.response_mime_type == "application/json"
    assert response.parsed.category == "billing"
    assert response.usage == TokenUsage(5, 3, 0, 0)


def test_gemini_tool_loop_replays_model_content():
    call = SimpleNamespace(id=None, name="lookup_customer", args={"id": "1"})
    part = types.Part.from_function_call(name="lookup_customer", args={"id": "1"})
    models = FakeGeminiModels(_gemini_response([part], calls=[call]))
    provider = _gemini(models)
    first = asyncio.run(provider.generate(_request(tools=(TOOL,), schema=TriageResult)))
    assert first.stop_reason == "tool_use" and first.tool_calls[0].id == "lookup_customer"
    assert models.calls[0]["config"].response_schema is None  # no JSON constraint alongside tools

    turns = (UserTurn("hello"), AssistantTurn(first), ToolResultsTurn((ToolResult("lookup_customer", "lookup_customer", "{}"),)))
    asyncio.run(provider.generate(_request(tools=(TOOL,), turns=turns)))
    contents = models.calls[1]["contents"]
    assert contents[1] is first.native
    assert contents[2].parts[0].function_response.name == "lookup_customer"


def test_gemini_safety_block_is_a_refusal():
    models = FakeGeminiModels(_gemini_response([types.Part.from_text(text="")], finish="SAFETY"))
    assert asyncio.run(_gemini(models).generate(_request())).stop_reason == "refusal"


# --- agents stay provider independent ----------------------------------------------------------


@pytest.mark.parametrize("provider", ["gemini", "anthropic", "openai"])
def test_triage_agent_works_with_every_provider(monkeypatch, provider):
    async def fake_generate(route, **kwargs):
        assert kwargs["schema"] is TriageResult
        return LLMResponse(provider, route.model, TRIAGE_JSON, [], "end", TokenUsage(3, 4),
                           parsed=TriageResult.model_validate_json(TRIAGE_JSON))

    monkeypatch.setattr(llm, "route", lambda agent, **_: router.Route(agent, provider, f"{provider}-model", "fast", "test"))
    monkeypatch.setattr(llm, "generate", fake_generate)
    usage, provenance = [], {}
    ticket = TicketData(id=1, customer_id=1, subject="Charged twice", body="Refund please",
                        created_at=dt.datetime.now(dt.timezone.utc))
    result = asyncio.run(triage(ticket, usage=usage, provenance=provenance))
    assert result.category == "billing"
    assert provenance == {"triage": "real_model"}
    assert (usage[0].provider, usage[0].model) == (provider, f"{provider}-model")
