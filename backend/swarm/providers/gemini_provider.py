"""Google Gemini adapter (google-genai SDK)."""

from __future__ import annotations

from functools import cached_property
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from swarm.providers.base import (
    AssistantTurn, LLMRequest, LLMResponse, ProviderError, TokenUsage, ToolCall, ToolResultsTurn, UserTurn,
    parse_json, replayable,
)

_REFUSALS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def configured(self) -> bool:
        return bool(self._api_key)

    @cached_property
    def _client(self) -> genai.Client:
        # Created lazily so importing the swarm never requires credentials.
        return genai.Client(api_key=self._api_key)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        config = types.GenerateContentConfig(system_instruction=request.system, max_output_tokens=request.max_tokens)
        if request.tools:
            config.tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.input_schema)
                for t in request.tools
            ])]
        elif request.schema is not None:
            # Gemini constrains JSON only without function calling; tool loops validate the final text instead.
            config.response_mime_type = "application/json"
            config.response_schema = request.schema
        try:
            response = await self._client.aio.models.generate_content(
                model=request.model, contents=_contents(request), config=config,
            )
        except errors.APIError as exc:
            code = getattr(exc, "code", None) or 0
            raise ProviderError(self.name, f"http_{code}", transient=code in (408, 429) or code >= 500) from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(self.name, type(exc).__name__, transient=True) from exc

        candidate = (response.candidates or [None])[0]
        content = getattr(candidate, "content", None)
        calls = [
            ToolCall(id=call.id or call.name, name=call.name, input=dict(call.args or {}))
            for call in response.function_calls or []
        ]
        text = _text(content)
        stop = _stop_reason(response, candidate, calls)
        parsed = None
        if request.schema is not None and stop == "end":
            parsed = response.parsed if isinstance(response.parsed, request.schema) else parse_json(request.schema, text)
        return LLMResponse(
            provider=self.name, model=request.model, text=text, tool_calls=calls, stop_reason=stop,
            usage=_usage(response), parsed=parsed, native=content,
        )


def _contents(request: LLMRequest) -> list[types.Content]:
    result: list[types.Content] = []
    for turn in request.turns:
        if not replayable(turn, "gemini"):
            raise ProviderError("gemini", "foreign_assistant_turn", transient=False)
        if isinstance(turn, UserTurn):
            result.append(types.Content(role="user", parts=[types.Part.from_text(text=turn.text)]))
        elif isinstance(turn, AssistantTurn):
            # Replay the model's own content (including thought signatures) unchanged.
            result.append(turn.response.native)
        elif isinstance(turn, ToolResultsTurn):
            parts = [
                types.Part.from_function_response(
                    name=r.name, response={"result": r.content, "is_error": r.is_error},
                )
                for r in turn.results
            ]
            if turn.note:
                parts.append(types.Part.from_text(text=turn.note))
            result.append(types.Content(role="user", parts=parts))
    return result


def _text(content: Any) -> str:
    parts = getattr(content, "parts", None) or []
    return "".join(part.text for part in parts if getattr(part, "text", None) and not getattr(part, "thought", False))


def _stop_reason(response: Any, candidate: Any, calls: list[ToolCall]) -> str:
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        return "refusal"
    reason = getattr(candidate, "finish_reason", None)
    name = getattr(reason, "name", str(reason or "")).upper()
    if name in _REFUSALS:
        return "refusal"
    if calls:
        return "tool_use"
    if name == "MAX_TOKENS":
        return "max_tokens"
    if name in ("STOP", ""):
        return "end"
    return "other"


def _usage(response: Any) -> TokenUsage | None:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=usage.prompt_token_count or 0,
        output_tokens=(usage.candidates_token_count or 0) + (getattr(usage, "thoughts_token_count", 0) or 0),
        cache_read_tokens=usage.cached_content_token_count or 0,
    )
