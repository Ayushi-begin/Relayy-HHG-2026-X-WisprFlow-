"""Choose the provider and model for each agent call.

Static routing sends each agent to the configured provider's tier model (triage = fast,
research/action = smart). Adaptive routing additionally escalates complex triage input to the
smart tier and, only when an explicit fallback provider is configured, avoids a provider whose
circuit is open. Every route records why it was chosen; nothing is substituted silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import config
from swarm.providers import health

Tier = Literal["fast", "smart"]
TIERS: dict[str, Tier] = {"triage": "fast", "research": "smart", "action": "smart"}
COMPLEX_INPUT = 0.7  # complexity score at which adaptive routing escalates to the smart tier


@dataclass(frozen=True)
class Route:
    agent: str
    provider: str
    model: str
    tier: Tier
    reason: str


def _model(provider: str, tier: Tier) -> str:
    return config.LLM_MODELS.get(provider, {}).get(tier, "")


def resolve(agent: str, *, complexity: float = 0.0, configured: set[str] | None = None) -> Route | None:
    """Return the route for this agent call, or None when no configured model can serve it."""
    tier = TIERS[agent]
    reason = f"{tier} tier for {agent}"
    adaptive = config.LLM_ROUTING == "adaptive"
    if adaptive and tier == "fast" and complexity >= COMPLEX_INPUT:
        tier, reason = "smart", "escalated to smart tier: complex or flagged input"

    explicit = config.LLM_ROUTES.get(agent, "")
    if explicit:
        provider, _, model = explicit.partition(":")
        reason = f"explicit LLM_ROUTE_{agent.upper()}"
    else:
        provider, model = config.LLM_PROVIDER, _model(config.LLM_PROVIDER, tier)

    fallback = config.LLM_FALLBACK_PROVIDER
    if adaptive and fallback and fallback != provider and health.circuit_open(provider):
        fallback_model = _model(fallback, tier)
        if fallback_model and (configured is None or fallback in configured):
            return Route(agent, fallback, fallback_model, tier, f"fallback: {provider} circuit open")

    if not provider or not model or (configured is not None and provider not in configured):
        return None
    return Route(agent, provider, model, tier, reason)
