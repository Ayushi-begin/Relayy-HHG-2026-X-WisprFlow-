"""Agent profiles, operator controls and performance statistics derived from stored runs."""

import datetime as dt
from collections import Counter, defaultdict
from typing import Any

from sqlmodel import Session, select

import audit
from db import engine
from models import AgentSetting, TicketRun
from swarm import controls, llm
from swarm.providers.router import TIERS
from swarm.state import TicketState, utcnow

AGENTS = ("triage", "research", "action")
RECENT_EXECUTIONS = 12

PROFILES: dict[str, dict[str, Any]] = {
    "triage": {
        "name": "Triage",
        "role": "Classifier",
        "description": "Classifies each ticket's category, urgency, sentiment and language, and flags "
                       "manipulation attempts before any other agent sees the ticket.",
        "inputs": ["Redacted ticket text"],
        "outputs": ["category", "urgency", "sentiment", "is_suspicious", "confidence"],
        "tools": [],
    },
    "research": {
        "name": "Research",
        "role": "Investigator",
        "description": "Checks the ticket's claims against records with read-only tools scoped to the "
                       "sender's own customer account, then reports factual findings.",
        "inputs": ["Triage result", "Redacted ticket text"],
        "outputs": ["key_findings", "customer", "payments", "past_tickets", "relevant_docs"],
        "tools": ["lookup_customer", "get_payments", "get_past_tickets", "search_help_docs"],
    },
    "action": {
        "name": "Action",
        "role": "Resolver",
        "description": "Proposes a decision and drafts the reply. Hard Python policy reviews every proposal; "
                       "the model can never authorize a refund or email on its own.",
        "inputs": ["Triage result", "Research findings"],
        "outputs": ["decision", "reply_draft", "refund proposal", "reasoning"],
        "tools": [],
    },
}


def load_controls() -> None:
    """Apply persisted operator controls to the in-process swarm at startup."""
    with Session(engine) as session:
        rows = session.exec(select(AgentSetting)).all()
    controls.replace({row.agent for row in rows if not row.model_enabled})


def set_model_enabled(agent: str, enabled: bool, actor: str) -> dict[str, Any]:
    if agent not in AGENTS:
        raise KeyError(agent)
    with Session(engine) as session:
        setting = session.get(AgentSetting, agent)
        previous = setting.model_enabled if setting else True
        if setting is None:
            setting = AgentSetting(agent=agent, model_enabled=enabled, updated_at=utcnow(), updated_by=actor)
        else:
            setting.model_enabled, setting.updated_at, setting.updated_by = enabled, utcnow(), actor
        session.add(setting)
        audit.record(session, actor=actor, action="agent.model_enabled", target=agent,
                     detail={"from": previous, "to": enabled})
        session.commit()
    controls.set_model_enabled(agent, enabled)
    return describe()[agent]


def _settings() -> dict[str, AgentSetting]:
    with Session(engine) as session:
        return {row.agent: row for row in session.exec(select(AgentSetting)).all()}


def _stats(runs: list[TicketRun]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {agent: {
        "runs": 0, "real_model": 0, "simulated": 0, "failed": 0, "calls": 0,
        "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "cost_known": True,
        "latencies": [], "models": Counter(), "last_run_at": None, "recent": [],
    } for agent in AGENTS}
    decisions: Counter[str] = Counter()
    for run in sorted(runs, key=lambda r: r.started_at, reverse=True):
        if not run.agent_state:
            continue
        state = TicketState.model_validate(run.agent_state)
        usage_by_agent: dict[str, list] = defaultdict(list)
        for usage in state.usage:
            usage_by_agent[usage.agent].append(usage)
        if state.action:
            decisions[state.action.decision] += 1
        for agent in AGENTS:
            source = state.provenance.get(agent)
            if source is None:
                continue
            entry = stats[agent]
            entry["runs"] += 1
            if source in ("real_model", "simulated", "failed"):
                entry[source] += 1
            entry["last_run_at"] = entry["last_run_at"] or run.started_at
            calls = usage_by_agent.get(agent, [])
            latency = sum(u.latency_ms for u in calls if u.latency_ms is not None) if calls else None
            for usage in calls:
                entry["calls"] += 1
                entry["input_tokens"] += usage.input_tokens
                entry["output_tokens"] += usage.output_tokens
                entry["models"][f"{usage.provider or 'unknown'}:{usage.model}"] += 1
                if usage.cost_usd is None:
                    entry["cost_known"] = False
                else:
                    entry["cost_usd"] += usage.cost_usd
            if latency:
                entry["latencies"].append(latency)
            if len(entry["recent"]) < RECENT_EXECUTIONS:
                entry["recent"].append({
                    "ticket_id": run.ticket_id, "run_id": run.id, "run_number": run.run_number,
                    "started_at": run.started_at.isoformat(), "provenance": source,
                    "error": state.provenance_errors.get(agent), "calls": len(calls), "latency_ms": latency,
                    "model": calls[-1].model if calls else None, "provider": calls[-1].provider if calls else None,
                })

    result = {}
    for agent, entry in stats.items():
        latencies = entry.pop("latencies")
        known = entry.pop("cost_known")
        runs_count = entry["runs"]
        result[agent] = {
            **entry,
            "success_rate": round(entry["real_model"] / runs_count, 4) if runs_count else None,
            "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
            "cost_usd": round(entry["cost_usd"], 5) if known and entry["calls"] else None,
            "models": dict(entry["models"].most_common()),
            "last_run_at": entry["last_run_at"].isoformat() if isinstance(entry["last_run_at"], dt.datetime) else None,
        }
    result["action"]["decisions"] = dict(decisions)
    return result


def describe() -> dict[str, dict[str, Any]]:
    with Session(engine) as session:
        runs = list(session.exec(select(TicketRun)).all())
    settings = _settings()
    stats = _stats(runs)
    described = {}
    for agent in AGENTS:
        route = llm.route(agent)
        setting = settings.get(agent)
        described[agent] = {
            "id": agent,
            **PROFILES[agent],
            "tier": TIERS[agent],
            "model_enabled": controls.model_enabled(agent),
            "updated_at": setting.updated_at.isoformat() if setting else None,
            "updated_by": setting.updated_by if setting else None,
            "route": route.__dict__ if route else None,
            "mode": "live" if route else "simulated",
            "unavailable_reason": None if route else llm.unavailable_reason(agent),
            "stats": stats[agent],
        }
    return described
