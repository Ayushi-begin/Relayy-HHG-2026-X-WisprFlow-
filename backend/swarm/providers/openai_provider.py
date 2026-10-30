"""OpenAI adapter (official `openai` SDK, Chat Completions)."""

from __future__ import annotations

import json
from functools import cached_property
from typing import Any

import openai

from swarm.providers.base import (
    AssistantTurn, LLMRequest, LLMResponse, ProviderError, TokenUsage, ToolCall, ToolResultsTurn, UserTurn,
    parse_json, replayable,
)

_STOP = {"stop": "end", "tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, *, timeout: float) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def configured(self) -> bool:
        return bool(self._api_key)

    @cached_property
    def _client(self) -> openai.AsyncOpenAI:
        # Retries are owned by swarm.llm so every attempt is visible to provider health.
        return openai.AsyncOpenAI(api_key=self._api_key, max_retries=0, timeout=self._timeout)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        params: dict[str, Any] = {
            "model": request.model,
            "max_completion_tokens": request.max_tokens,
            "messages": _messages(request),
        }
        if request.tools:
            params["tools"] = [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description, "parameters": t.input_schema,
                }}
                for t in request.tools
            ]
        try:
            if request.schema is not None and not request.tools:
                response = await self._client.chat.completions.parse(response_format=request.schema, **params)
            else:
                if request.schema is not None:
                    # Strict parsing requires strict tools; the tool loop asks for JSON and validates it here.
                    params["response_format"] = {"type": "json_object"}
                response = await self._client.chat.completions.create(**params)
        except openai.APIStatusError as exc:
            status = exc.status_code
            raise ProviderError(self.name, f"http_{status}", transient=status in (408, 409, 429) or status >= 500) from exc
        except openai.APIConnectionError as exc:  # includes APITimeoutError
            raise ProviderError(self.name, type(exc).__name__, transient=True) from exc

        choice = response.choices[0]
        message = choice.message
        calls = [
            ToolCall(id=call.id, name=call.function.name, input=_arguments(call.function.arguments))
            for call in message.tool_calls or []
            if getattr(call, "function", None) is not None
        ]
        stop = "refusal" if getattr(message, "refusal", None) else _STOP.get(choice.finish_reason or "", "other")
        text = message.content or ""
        parsed = None
        if request.schema is not None and stop == "end":
            parsed = getattr(message, "parsed", None) or parse_json(request.schema, text)
        native: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            native["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.input)}}
                for c in calls
            ]
        usage = response.usage
        cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) if usage else 0
        return LLMResponse(
            provider=self.name, model=response.model or request.model, text=text, tool_calls=calls, stop_reason=stop,
            usage=TokenUsage(
                input_tokens=usage.prompt_tokens or 0, output_tokens=usage.completion_tokens or 0,
                cache_read_tokens=cached or 0,
            ) if usage else None,
            parsed=parsed, native=native,
        )


def _arguments(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _messages(request: LLMRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": request.system}]
    for turn in request.turns:
        if not replayable(turn, "openai"):
            raise ProviderError("openai", "foreign_assistant_turn", transient=False)
        if isinstance(turn, UserTurn):
            messages.append({"role": "user", "content": turn.text})
        elif isinstance(turn, AssistantTurn):
            messages.append(turn.response.native)
        elif isinstance(turn, ToolResultsTurn):
            # Chat Completions has no error flag on tool messages, so mark failures in the content.
            messages += [
                {"role": "tool", "tool_call_id": r.call_id, "content": f"ERROR: {r.content}" if r.is_error else r.content}
                for r in turn.results
            ]
            if turn.note:
                messages.append({"role": "user", "content": turn.note})
    return messages
