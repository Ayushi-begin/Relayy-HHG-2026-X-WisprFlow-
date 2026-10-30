"""Provider-neutral request/response types shared by every LLM adapter.

Agents build an `LLMRequest` from neutral turns; each adapter translates it to its SDK and
returns an `LLMResponse`. An adapter's own assistant turn is replayed through `native` so
provider-specific blocks (for example Claude thinking blocks) survive multi-turn tool loops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel

ProviderName = Literal["gemini", "anthropic", "openai"]
StopReason = Literal["end", "tool_use", "refusal", "max_tokens", "other"]
Effort = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class UserTurn:
    text: str


@dataclass(frozen=True)
class ToolResultsTurn:
    results: tuple[ToolResult, ...]
    note: str | None = None  # extra user text sent alongside the results


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class LLMResponse:
    provider: ProviderName
    model: str
    text: str
    tool_calls: list[ToolCall]
    stop_reason: StopReason
    usage: TokenUsage | None
    latency_ms: int = 0
    parsed: Any = None
    # Provider-native assistant content, replayed unchanged by the same adapter only.
    native: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class AssistantTurn:
    response: LLMResponse


Turn = UserTurn | AssistantTurn | ToolResultsTurn


@dataclass(frozen=True)
class LLMRequest:
    model: str
    system: str
    turns: tuple[Turn, ...]
    max_tokens: int
    schema: type[BaseModel] | None = None
    tools: tuple[ToolSpec, ...] = ()
    effort: Effort | None = None


class ProviderError(Exception):
    """A normalized adapter failure. `transient` errors are safe to retry: model calls have no side effects."""

    def __init__(self, provider: str, kind: str, *, transient: bool) -> None:
        super().__init__(f"{provider} {kind}")
        self.provider = provider
        self.kind = kind
        self.transient = transient


class LLMProvider(Protocol):
    name: ProviderName

    def configured(self) -> bool: ...

    async def generate(self, request: LLMRequest) -> LLMResponse: ...


def parse_json(schema: type[BaseModel], text: str) -> BaseModel | None:
    """Validate model text against the agent schema. Model output is never trusted unvalidated."""
    try:
        return schema.model_validate_json(_strip_fence(text))
    except ValueError:
        return None


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def replayable(turn: Turn, provider: str) -> bool:
    """Assistant turns can only be replayed to the adapter that produced them."""
    return not isinstance(turn, AssistantTurn) or turn.response.provider == provider
