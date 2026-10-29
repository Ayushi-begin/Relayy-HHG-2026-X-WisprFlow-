"""Ticket lifecycle: run the swarm in the background, persist results, apply human decisions."""

import asyncio
import datetime as dt
import logging
from collections import Counter
from typing import Literal

from sqlmodel import Session, select

from db import engine
from events import bus
from integrations import slack
from models import Ticket, TicketStatus
from swarm.orchestrator import run_pipeline
from swarm.state import Event, TicketData, TicketState, utcnow

log = logging.getLogger(__name__)

HumanAction = Literal["approved", "edited", "rejected"]

# Swarm runs in progress: ticket id -> live state. Lets GET /tickets/{id} and the
# SSE stream see events before the final state is written to the DB.
_live: dict[int, TicketState] = {}
_tasks: set[asyncio.Task] = set()


# --- Reading -------------------------------------------------------------------


def load_state(ticket: Ticket) -> TicketState:
    """Live state if the swarm is running, else the persisted state, else an empty one."""
    if ticket.id in _live:
        return _live[ticket.id]
    if ticket.swarm_state:
        return TicketState.model_validate(ticket.swarm_state)
    return TicketState(ticket=TicketData.model_validate(ticket.model_dump()))


def is_running(ticket_id: int) -> bool:
    return ticket_id in _live


# --- Processing ----------------------------------------------------------------


def start_processing(ticket_id: int) -> bool:
    """Start the swarm for a ticket in the background. False if it is already running."""
    if ticket_id in _live:
        return False
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        ticket.status = TicketStatus.processing
        ticket.swarm_state = ticket.resolution = ticket.resolved_by = ticket.final_reply = None
        ticket.updated_at = utcnow()
        # Snapshot before commit: committing expires the row, and model_dump() won't reload it.
        state = TicketState(ticket=TicketData.model_validate(ticket.model_dump()))
        session.add(ticket)
        session.commit()

    _live[ticket_id] = state
    bus.publish_status(ticket_id, TicketStatus.processing)
    task = asyncio.create_task(_process(ticket_id, state))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


def _append(ticket_id: int, state: TicketState, agent, status, message: str) -> None:
    event = Event(agent=agent, status=status, message=message)
    state.events.append(event)
    bus.publish_event(ticket_id, len(state.events) - 1, event)


def _save(ticket_id: int, state: TicketState, **fields) -> None:
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        ticket.swarm_state = state.model_dump(mode="json")
        ticket.updated_at = utcnow()
        for name, value in fields.items():
            setattr(ticket, name, value)
        session.add(ticket)
        session.commit()
    if "status" in fields:
        bus.publish_status(ticket_id, fields["status"], fields.get("resolution"))


async def _process(ticket_id: int, state: TicketState) -> None:
    try:
        index = 0
        async for event in run_pipeline(state):
            bus.publish_event(ticket_id, index, event)
            index += 1

        action = state.action
        if action and action.decision in ("auto_resolve", "auto_refund"):
            resolution = "auto_refunded" if action.decision == "auto_refund" else "auto_resolved"
            _append(ticket_id, state, "orchestrator", "done", "📨 Reply sent to the customer automatically.")
            _save(ticket_id, state, status=TicketStatus.resolved, resolution=resolution, resolved_by="ai",
                  final_reply=action.reply_draft)
            return

        # Escalate. Save first so a fast Slack click finds the ticket waiting for review.
        _save(ticket_id, state, status=TicketStatus.escalated)
        if not slack.is_configured():
            _append(ticket_id, state, "orchestrator", "done", "🙋 Escalated for human review (Slack is not configured).")
        else:
            try:
                where = await slack.post_escalation(ticket_id, state)
                _append(ticket_id, state, "orchestrator", "done", f"📣 Escalated to Slack ({where}) for review.")
            except (slack.SlackError, OSError) as exc:
                log.exception("Slack escalation failed for ticket %s", ticket_id)
                _append(ticket_id, state, "orchestrator", "error", f"Escalated, but posting to Slack failed: {exc}")
        _save(ticket_id, state)
    except Exception as exc:
        log.exception("Processing ticket %s failed", ticket_id)
        _append(ticket_id, state, "orchestrator", "error", f"Processing failed: {type(exc).__name__}: {exc}")
        _save(ticket_id, state, status=TicketStatus.escalated)
    finally:
        _live.pop(ticket_id, None)


def recover_interrupted() -> None:
    """Tickets left 'processing' by a server restart go back to 'open'."""
    with Session(engine) as session:
        for ticket in session.exec(select(Ticket).where(Ticket.status == TicketStatus.processing)):
            ticket.status = TicketStatus.open
            session.add(ticket)
        session.commit()


# --- Human decisions (from Slack) ----------------------------------------------


def apply_human_action(
    ticket_id: int, action: HumanAction, user: str, reply: str | None = None
) -> tuple[Ticket, TicketState, bool]:
    """Record a reviewer's decision. Returns (ticket, state, applied); applied is False if
    the ticket was not waiting for review (e.g. someone already handled it)."""
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            raise KeyError(ticket_id)
        state = load_state(ticket)
        if ticket.status != TicketStatus.escalated or ticket_id in _live:
            return ticket, state, False

        draft = state.action.reply_draft if state.action else None
        if action == "rejected":
            ticket.status = TicketStatus.rejected
            message = f"❌ Rejected by @{user} in Slack; needs manual handling."
        else:
            ticket.status = TicketStatus.resolved
            ticket.final_reply = reply if action == "edited" else draft
            message = (f"✏️ Edited and approved by @{user} in Slack; reply sent to the customer." if action == "edited"
                       else f"✅ Approved by @{user} in Slack; reply sent to the customer.")
        ticket.resolution = action
        ticket.resolved_by = user

        event = Event(agent="human", status="done", message=message)
        state.events.append(event)
        ticket.swarm_state = state.model_dump(mode="json")
        ticket.updated_at = utcnow()
        session.add(ticket)
        session.commit()
        session.refresh(ticket)

    bus.publish_event(ticket_id, len(state.events) - 1, event)
    bus.publish_status(ticket_id, ticket.status, ticket.resolution)
    return ticket, state, True


# --- Metrics ---------------------------------------------------------------------

MINUTES_PER_MANUAL_TICKET = 8


def _decided_at(state: TicketState) -> dt.datetime | None:
    """When the action step finished (or the swarm's last event, if it never did)."""
    swarm = [e for e in state.events if e.agent != "human"]
    done = next((e for e in reversed(swarm) if e.agent == "action" and e.status in ("done", "error")), None)
    return (done or swarm[-1]).timestamp if swarm else None


def metrics() -> dict:
    with Session(engine) as session:
        tickets = session.exec(select(Ticket)).all()

    status = Counter(t.status.value for t in tickets)
    resolution = Counter(t.resolution for t in tickets if t.resolution)
    decisions, categories, rules, cost_by_agent = Counter(), Counter(), Counter(), Counter()
    timeline, durations = [], []
    suspicious = 0
    refunded = total_cost = 0.0

    for t in tickets:
        if not t.swarm_state or t.id in _live:
            continue
        state = TicketState.model_validate(t.swarm_state)
        if state.action is None:
            continue
        decisions[state.action.decision] += 1
        rules.update(state.action.rules_fired)
        refunded += state.action.refund_amount or 0
        category = state.triage.category if state.triage else "other"
        categories[category] += 1
        suspicious += bool(state.triage and state.triage.is_suspicious)
        for u in state.usage:
            cost_by_agent[u.agent] += u.cost_usd
        total_cost += state.cost_usd

        decided_at = _decided_at(state)
        seconds = (decided_at - state.events[0].timestamp).total_seconds() if decided_at else None
        if seconds is not None:
            durations.append(seconds)
        timeline.append({
            "ticket_id": t.id,
            "decided_at": decided_at.isoformat() if decided_at else None,
            "decision": state.action.decision,
            "category": category,
            "seconds": round(seconds, 2) if seconds is not None else None,
            "cost_usd": round(state.cost_usd, 5),
        })

    processed = sum(decisions.values())
    automated = resolution["auto_resolved"] + resolution["auto_refunded"]
    reviewed = resolution["approved"] + resolution["edited"] + resolution["rejected"]

    def rate(n: int, d: int) -> float | None:
        return round(n / d, 4) if d else None

    return {
        "tickets_total": len(tickets),
        "processed": processed,
        "by_status": dict(status),
        "by_decision": dict(decisions),
        "by_category": dict(categories.most_common()),
        "auto_resolved_rate": rate(automated, processed),
        "escalated_rate": rate(decisions["escalate"], processed),
        "avg_time_to_decision_seconds": round(sum(durations) / len(durations), 2) if durations else None,
        "minutes_per_manual_ticket": MINUTES_PER_MANUAL_TICKET,
        "agent_hours_saved": round(automated * MINUTES_PER_MANUAL_TICKET / 60, 2),
        "total_cost_usd": round(total_cost, 4),
        "cost_per_ticket_usd": round(total_cost / processed, 5) if processed else None,
        "cost_by_agent": {k: round(v, 5) for k, v in cost_by_agent.items()},
        "escalations_by_rule": dict(rules.most_common()),
        "human_reviews": {"approved": resolution["approved"], "edited": resolution["edited"],
                          "rejected": resolution["rejected"]},
        "ai_reply_acceptance_rate": rate(resolution["approved"], reviewed),
        "suspicious_flagged": suspicious,
        "auto_refunded_total": round(refunded, 2),
        "timeline": sorted(timeline, key=lambda p: p["decided_at"] or ""),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
