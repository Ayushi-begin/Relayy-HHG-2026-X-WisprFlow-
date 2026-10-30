"""Anthropic Claude adapter (official `anthropic` SDK)."""

from __future__ import annotations

from functools import cached_property
from typing import Any

import anthropic

from swarm.providers.base import (
    AssistantTurn, LLMRequest, LLMResponse, ProviderError, TokenUsage, ToolCall, ToolResultsTurn, UserTurn,
    replayable,
)

_STOP = {"end_turn": "end", "stop_sequence": "end", "tool_use": "tool_use", "refusal": "refusal", "max_tokens": "max_tokens"}
# Models that accept the server-side refusal fallback. The served model is recorded from the response.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, timeout: float, refusal_fallback: bool = True) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._refusal_fallback = refusal_fallback

    def configured(self) -> bool:
        return bool(self._api_key)

    @cached_property
    def _client(self) -> anthropic.AsyncAnthropic:
        # Retries are owned by swarm.llm so every attempt is visible to provider health.
        return anthropic.AsyncAnthropic(api_key=self._api_key, max_retries=0, timeout=self._timeout)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": _messages(request),
        }
        if request.effort:
            params["output_config"] = {"effort": request.effort}
        if request.tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in request.tools
            ]
        if self._refusal_fallback and request.model in _FALLBACK_MODELS:
            params["fallbacks"] = "default"
            params["betas"] = [_FALLBACK_BETA]
        try:
            if request.schema is not None:
                response = await self._client.beta.messages.parse(output_format=request.schema, **params)
            else:
                response = await self._client.beta.messages.create(**params)
        except anthropic.APIStatusError as exc:
            status = exc.status_code
            raise ProviderError(self.name, f"http_{status}", transient=status in (408, 409, 429) or status >= 500) from exc
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            raise ProviderError(self.name, type(exc).__name__, transient=True) from exc

        calls = [ToolCall(id=b.id, name=b.name, input=dict(b.input)) for b in response.content if b.type == "tool_use"]
        text = next((b.text for b in reversed(response.content) if b.type == "text"), "")
        stop = _STOP.get(response.stop_reason or "", "other")
        parsed = getattr(response, "parsed_output", None) if stop == "end" else None
        usage = response.usage
        return LLMResponse(
            provider=self.name,
            model=response.model or request.model,  # differs from the request when a fallback served it
            text=text,
            tool_calls=calls,
            stop_reason=stop,
            usage=TokenUsage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            ) if usage else None,
            parsed=parsed,
            native=response.content,
        )


def _messages(request: LLMRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for turn in request.turns:
        if not replayable(turn, "anthropic"):
            raise ProviderError("anthropic", "foreign_assistant_turn", transient=False)
        if isinstance(turn, UserTurn):
            messages.append({"role": "user", "content": turn.text})
        elif isinstance(turn, AssistantTurn):
            # Pass the assistant content back unchanged (thinking blocks included); history stays append-only.
            messages.append({"role": "assistant", "content": turn.response.native})
        elif isinstance(turn, ToolResultsTurn):
            content: list[dict[str, Any]] = [
                {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
                for r in turn.results
            ]
            if turn.note:
                content.append({"type": "text", "text": turn.note})
            messages.append({"role": "user", "content": content})
    return messages
