"""Durable ticket runs, review decisions, and ticket projections."""

import asyncio
import datetime as dt
import logging
import uuid
from collections import Counter
from typing import Any, Literal

from sqlalchemy import func, update
from sqlmodel import Session, select

from db import engine
from events import bus
from models import DeliveryOperation, Ticket, TicketEvent, TicketRun, TicketStatus
from swarm.orchestrator import run_pipeline
from swarm.state import Event, TicketData, TicketState, utcnow

log = logging.getLogger(__name__)
HumanAction = Literal["approved", "edited", "rejected"]
_live: dict[int, TicketState] = {}
_tasks: set[asyncio.Task] = set()


def _status(value: TicketStatus | str) -> str:
    return value.value if isinstance(value, TicketStatus) else str(value)


def _ticket_state(ticket: Ticket, run: TicketRun | None = None) -> TicketState:
    data = (run.agent_state if run else None) or ticket.swarm_state
    if data:
        return TicketState.model_validate(data)
    return TicketState(ticket=TicketData.model_validate(ticket.model_dump()))


def _execution_mode(state: TicketState) -> str:
    """Project stage provenance without claiming a model run that did not occur."""
    provenance = getattr(state, "provenance", {})
    values = [provenance.get(stage) for stage in ("triage", "research", "action")]
    if values and all(value == "real_model" for value in values):
        return "real_model"
    if any(value == "failed" for value in values):
        return "failed"
    return "simulated"


def load_state(ticket: Ticket) -> TicketState:
    if ticket.id in _live:
        return _live[ticket.id]
    return _ticket_state(ticket)


def is_running(ticket_id: int) -> bool:
    return ticket_id in _live


def _append_event(session: Session, ticket: Ticket, run_id: str | None, kind: str, data: dict[str, Any]) -> TicketEvent:
    event = TicketEvent(ticket_id=ticket.id, run_id=run_id, kind=kind, timestamp=utcnow(), data=data)
    session.add(event)
    session.flush()
    return event


def _event_payload(event: TicketEvent) -> tuple[int, int, dict[str, Any]]:
    """Copy ORM fields before commit expires the event instance."""
    return event.ticket_id, event.id or 0, {
        "run_id": event.run_id, "kind": event.kind, "timestamp": event.timestamp.isoformat(), "data": event.data,
    }


def _publish(event: tuple[int, int, dict[str, Any]]) -> None:
    ticket_id, event_id, data = event
    bus.publish_ticket_event(ticket_id, event_id, {
        **data,
    })


def _run_for(session: Session, ticket: Ticket) -> TicketRun | None:
    return session.get(TicketRun, ticket.current_run_id) if ticket.current_run_id else None


def _has_operations(session: Session, ticket_id: int) -> bool:
    return session.exec(select(DeliveryOperation.id).where(DeliveryOperation.ticket_id == ticket_id).limit(1)).first() is not None


def start_processing(ticket_id: int) -> bool:
    """Create a new immutable run before starting agent work.

    Reruns are intentionally prohibited after any delivery operation exists.  Creating
    the TicketRun and publishing its status happens before the asynchronous pipeline so
    Slack/web clients never observe a button for an uncommitted run.
    """
    if ticket_id in _live:
        return False
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None or _has_operations(session, ticket_id):
            return False
        expected_version = ticket.version
        current = _run_for(session, ticket)
        if _status(ticket.status) == TicketStatus.processing.value:
            return False
        input_snapshot = TicketData.model_validate(ticket.model_dump()).model_dump(mode="json")
        number = (session.exec(select(func.max(TicketRun.run_number)).where(TicketRun.ticket_id == ticket_id)).one() or 0) + 1
        run_id = str(uuid.uuid4())
        now = utcnow()
        run = TicketRun(
            id=run_id, ticket_id=ticket_id, run_number=number, state="processing", input_snapshot=input_snapshot,
            started_at=now,
        )
        result = session.execute(update(Ticket).where(
            Ticket.id == ticket_id, Ticket.version == expected_version, Ticket.status != TicketStatus.processing,
        ).values(current_run_id=run_id, status=TicketStatus.processing, version=expected_version + 1,
                 swarm_state=None, resolution=None, resolved_by=None, final_reply=None,
                 fulfillment_state="not_requested", manual_followup_required=False, updated_at=now))
        if result.rowcount != 1:
            session.rollback()
            return False
        if current and current.state == "reviewable":
            current.state = "superseded"
            current.ended_at = now
            session.add(current)
        session.add(run)
        session.flush()
        # Refresh only after the conditional claim has won so helper writes cannot
        # accidentally flush stale fields from the original ticket read.
        session.expire(ticket)
        ticket = session.get(Ticket, ticket_id)
        assert ticket is not None
        state = TicketState(ticket=TicketData.model_validate(ticket.model_dump()))
        event = _append_event(session, ticket, run_id, "status", {"status": "processing", "version": ticket.version})
        event_payload = _event_payload(event)
        session.commit()

    _live[ticket_id] = state
    _publish(event_payload)
    new_version = expected_version + 1
    bus.publish_status(ticket_id, "processing", run_id=run_id, version=new_version)
    task = asyncio.create_task(_process(ticket_id, run_id, state))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


def _record_agent_event(ticket_id: int, run_id: str, state: TicketState, event: Event) -> None:
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None or ticket.current_run_id != run_id:
            return
        run = session.get(TicketRun, run_id)
        if run is None or run.state != "processing":
            return
        snapshot = state.model_dump(mode="json")
        run.agent_state = snapshot
        ticket.swarm_state = snapshot
        ticket.updated_at = utcnow()
        durable = _append_event(session, ticket, run_id, "agent", event.model_dump(mode="json"))
        payload = _event_payload(durable)
        session.add(run)
        session.add(ticket)
        session.commit()
    _publish(payload)


def _finalize_run(ticket_id: int, run_id: str, state: TicketState, *, status: TicketStatus, run_state: str,
                  resolution: str | None = None, actor: str | None = None) -> Ticket | None:
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        run = session.get(TicketRun, run_id)
        if not ticket or not run or ticket.current_run_id != run_id or run.state != "processing":
            return None
        run.agent_state = state.model_dump(mode="json")
        run.execution_mode = _execution_mode(state)
        run.state = run_state
        run.ended_at = utcnow() if run_state != "reviewable" else None
        ticket.swarm_state = run.agent_state
        ticket.status = status
        ticket.resolution = resolution
        ticket.resolved_by = actor
        ticket.updated_at = utcnow()
        ticket.version += 1
        durable = _append_event(session, ticket, run_id, "status", {
            "status": _status(status), "resolution": resolution, "version": ticket.version,
        })
        payload = _event_payload(durable)
        session.add(run)
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
    _publish(payload)
    bus.publish_status(ticket_id, _status(status), resolution, run_id=run_id, version=ticket.version)
    return ticket


async def _process(ticket_id: int, run_id: str, state: TicketState) -> None:
    authorization_started = False
    try:
        async for event in run_pipeline(state):
            _record_agent_event(ticket_id, run_id, state, event)
        action = state.action
        if action and action.decision in ("auto_resolve", "auto_refund"):
            # Fulfillment authorizes only policy/provenance-valid actions. It may create
            # blocked records, which is still a durable, truthful terminal analysis result.
            authorization_started = True
            _authorize_automatic(ticket_id, run_id, state)
        else:
            ticket = _finalize_run(ticket_id, run_id, state, status=TicketStatus.escalated, run_state="reviewable")
            if ticket:
                await _post_slack_review(ticket, run_id, state)
    except Exception as exc:
        log.exception("Processing ticket %s failed", ticket_id)
        event = Event(agent="orchestrator", status="error", message=f"Processing failed: {type(exc).__name__}")
        state.events.append(event)
        _record_agent_event(ticket_id, run_id, state, event)
        if authorization_started:
            _mark_authorization_failed(ticket_id, run_id, state, exc)
        else:
            _finalize_run(ticket_id, run_id, state, status=TicketStatus.escalated, run_state="reviewable")
    finally:
        _live.pop(ticket_id, None)


def _mark_authorization_failed(ticket_id: int, run_id: str, state: TicketState, error: Exception) -> None:
    """Freeze failed automatic authorization as manual-only, never as a fresh review.

    A reservation conflict may mean another ticket already owns a provider target. The
    original auto-refund confirmation is therefore unsafe to display as approvable.
    """
    reason = "refund_target_reserved" if "different frozen operation" in str(error).lower() else "authorization_failed"
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        run = session.get(TicketRun, run_id)
        if not ticket or not run or ticket.current_run_id != run_id or run.state != "processing":
            return
        snapshot = state.model_dump(mode="json")
        run.agent_state = snapshot
        run.execution_mode = _execution_mode(state)
        run.state = "authorization_failed"
        run.ended_at = utcnow()
        run.authorized_plan = {"source": "ai", "authorization_error": reason}
        ticket.swarm_state = snapshot
        ticket.status = TicketStatus.escalated
        ticket.resolution = reason
        ticket.final_reply = None
        ticket.fulfillment_state = "blocked"
        ticket.manual_followup_required = True
        ticket.updated_at = utcnow()
        ticket.version += 1
        durable = _append_event(session, ticket, run_id, "authorization_failed", {
            "reason": reason, "status": "escalated", "version": ticket.version,
        })
        payload = _event_payload(durable)
        session.add(run)
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
    _publish(payload)
    bus.publish_status(ticket_id, _status(ticket.status), ticket.resolution, run_id=run_id, version=ticket.version)


async def _post_slack_review(ticket: Ticket, run_id: str, state: TicketState) -> None:
    """Post only after the reviewable state and revision are durable."""
    try:
        from integrations import slack
        if not slack.is_configured():
            return
        target = await slack.post_escalation(ticket.id, state, run_id, ticket.version)
        refresh_needed = False
        superseded = False
        with Session(engine) as session:
            run = session.get(TicketRun, run_id)
            current = session.get(Ticket, ticket.id)
            if run and current:
                run.slack_target = target
                superseded = current.current_run_id != run_id
                refresh_needed = superseded or run.state != "reviewable"
                session.add(run)
                session.commit()
        # A web decision can arrive while Slack is still creating the message.
        # Store the late target, then remove controls for the obsolete review.
        if refresh_needed:
            await slack.refresh_review(ticket.id, run_id, "This analysis run was superseded." if superseded else None)
    except Exception:
        # A Slack transport failure cannot invalidate the persisted web review.
        log.exception("Could not post Slack review for ticket %s", ticket.id)


def _authorize_automatic(ticket_id: int, run_id: str, state: TicketState) -> None:
    """Freeze automatic work. The fulfillment service decides whether it is blocked."""
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        run = session.get(TicketRun, run_id)
        if not ticket or not run or ticket.current_run_id != run_id or run.state != "processing":
            return
        ticket.status = TicketStatus.pending
        ticket.version += 1
        ticket.updated_at = utcnow()
        run.agent_state = state.model_dump(mode="json")
        run.execution_mode = _execution_mode(state)
        run.state = "authorized"
        run.ended_at = utcnow()
        run.review_outcome = "automatic"
        run.review_channel = "ai"
        run.review_actor = "ai"
        run.authorized_plan = {"reply": getattr(state.action, "reply_draft", None), "source": "ai"}
        # Deliberately lazy: the service is the main-owned fulfillment boundary and
        # imports provider adapters.  It must not commit this transaction.
        from fulfillment.service import authorize
        operations = authorize(session, ticket, state, state.action.reply_draft, "ai")
        durable = _append_event(session, ticket, run_id, "authorization", {
            "action": "automatic", "operations": [op.id for op in operations], "version": ticket.version,
        })
        payload = _event_payload(durable)
        session.add(run)
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
    _publish(payload)
    bus.publish_status(ticket_id, _status(ticket.status), ticket.resolution, run_id=run_id, version=ticket.version)


def apply_human_action(
    ticket_id: int, action: HumanAction, actor: str, *, run_id: str, expected_version: int,
    reply: str | None = None, channel: str = "web",
) -> tuple[Ticket, TicketState, bool]:
    """Atomically claim a review revision and freeze its reply/operations.

    The conditional update is the authority: simultaneous web and Slack decisions cannot
    both authorize.  Approval intentionally uses the stored draft, never a client reply;
    an escalated refund proposal remains a holding-email authorization only.
    """
    if action == "edited" and not (reply or "").strip():
        raise ValueError("Edited replies cannot be blank")
    published: list[tuple[int, int, dict[str, Any]]] = []
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            raise KeyError(ticket_id)
        run = session.get(TicketRun, run_id)
        if (not run or ticket.current_run_id != run_id or ticket.version != expected_version or
                run.state != "reviewable" or run.review_outcome is not None or _has_operations(session, ticket_id)):
            return ticket, _ticket_state(ticket, run), False
        state = _ticket_state(ticket, run)
        draft = getattr(state.action, "reply_draft", None) if state.action else None
        if action != "rejected" and not (draft or "").strip() and action != "edited":
            raise ValueError("The current run has no reviewable reply")
        result = session.execute(update(Ticket).where(
            Ticket.id == ticket_id, Ticket.current_run_id == run_id, Ticket.version == expected_version,
            Ticket.status == TicketStatus.escalated,
        ).values(version=Ticket.version + 1, updated_at=utcnow(), status=TicketStatus.rejected if action == "rejected" else TicketStatus.pending,
                 resolution=action, resolved_by=actor, final_reply=None if action == "rejected" else (reply.strip() if action == "edited" else draft)))
        if result.rowcount != 1:
            session.rollback()
            return ticket, state, False
        run_result = session.execute(update(TicketRun).where(
            TicketRun.id == run_id, TicketRun.ticket_id == ticket_id, TicketRun.state == "reviewable",
            TicketRun.review_outcome.is_(None),
        ).values(review_revision=TicketRun.review_revision + 1, review_outcome=action, review_channel=channel,
                 review_actor=actor, reviewed_at=utcnow(), state="rejected" if action == "rejected" else "authorized",
                 ended_at=utcnow()))
        if run_result.rowcount != 1:
            session.rollback()
            return ticket, state, False
        session.expire_all()
        ticket = session.get(Ticket, ticket_id)
        run = session.get(TicketRun, run_id)
        assert ticket and run
        run.authorized_plan = {"reply": ticket.final_reply, "source": "edited" if action == "edited" else "approved"}
        if action == "rejected":
            ticket.manual_followup_required = True
        else:
            from fulfillment.service import authorize
            # authorize receives the persisted draft/edited reply and must never recover
            # a refused model refund from action.proposal.
            authorize(session, ticket, state, ticket.final_reply or "", "edited" if action == "edited" else "approved")
        durable = _append_event(session, ticket, run_id, "review", {
            "action": action, "actor": actor, "channel": channel, "version": ticket.version,
            "review_revision": run.review_revision,
        })
        published.append(_event_payload(durable))
        session.add(run)
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
    for durable in published:
        _publish(durable)
    bus.publish_status(ticket_id, _status(ticket.status), ticket.resolution, run_id=run_id, version=ticket.version)
    return ticket, state, True


def request_recovery(ticket_id: int, operation_id: str, expected_version: int, action: Literal["retry", "reconcile"]) -> None:
    """Delegate the server-authorized recovery decision to fulfillment in one transaction."""
    # The fulfillment boundary owns and commits the conditional retry/reconcile
    # claim, including its version increment.
    from fulfillment.service import request_recovery as _request_recovery
    _request_recovery(ticket_id, operation_id, expected_version, action)


def operation_projection(operation: DeliveryOperation, session: Session) -> dict[str, Any]:
    from fulfillment.service import serialize_operation
    return serialize_operation(operation, session)


def serialize_ticket(session: Session, ticket: Ticket) -> dict[str, Any]:
    run = _run_for(session, ticket)
    state = load_state(ticket) if ticket.id in _live else _ticket_state(ticket, run)
    operations = session.exec(select(DeliveryOperation).where(DeliveryOperation.ticket_id == ticket.id).order_by(DeliveryOperation.created_at)).all()
    action = state.action
    can_review = bool(run and run.state == "reviewable" and _status(ticket.status) == "escalated" and
                      action and getattr(action, "reply_draft", "").strip() and not operations)
    return {
        "id": ticket.id, "status": _status(ticket.status), "resolution": ticket.resolution,
        "resolved_by": ticket.resolved_by, "final_reply": ticket.final_reply, "running": is_running(ticket.id),
        "state": state, "run_id": ticket.current_run_id, "version": ticket.version,
        "execution_mode": run.execution_mode if run else "legacy", "fulfillment_state": ticket.fulfillment_state,
        "manual_followup_required": ticket.manual_followup_required, "can_review": can_review,
        "can_reprocess": not is_running(ticket.id) and not operations and _status(ticket.status) != "processing",
        "operations": [operation_projection(op, session) for op in operations], "legacy_local_only": ticket.legacy_local_only,
    }


def run_summaries(session: Session, ticket_id: int) -> list[dict[str, Any]]:
    runs = session.exec(select(TicketRun).where(TicketRun.ticket_id == ticket_id).order_by(TicketRun.run_number)).all()
    if runs:
        return [{"id": run.id, "run_number": run.run_number, "state": run.state,
                 "execution_mode": run.execution_mode, "started_at": run.started_at, "ended_at": run.ended_at,
                 "review_outcome": run.review_outcome, "review_actor": run.review_actor,
                 "reviewed_at": run.reviewed_at, "input_snapshot": run.input_snapshot,
                 "agent_state": run.agent_state} for run in runs]
    ticket = session.get(Ticket, ticket_id)
    if ticket and ticket.legacy_local_only:
        return [{"id": f"legacy-ticket-{ticket_id}", "run_number": 0, "state": "legacy_local_only",
                 "execution_mode": "legacy", "started_at": ticket.created_at, "ended_at": ticket.updated_at,
                 "review_outcome": ticket.resolution, "review_actor": ticket.resolved_by,
                 "reviewed_at": ticket.updated_at,
                 "input_snapshot": TicketData.model_validate(ticket.model_dump()).model_dump(mode="json"),
                 "agent_state": ticket.swarm_state}]
    return []


def recover_interrupted() -> None:
    """Keep interrupted runs as audit history; never revive their model output."""
    with Session(engine) as session:
        runs = session.exec(select(TicketRun).where(TicketRun.state == "processing")).all()
        for run in runs:
            run.state = "interrupted"
            run.ended_at = utcnow()
            ticket = session.get(Ticket, run.ticket_id)
            if ticket and ticket.current_run_id == run.id:
                ticket.status = TicketStatus.open
                ticket.updated_at = utcnow()
                ticket.version += 1
                session.add(ticket)
            session.add(run)
        session.commit()


MINUTES_PER_MANUAL_TICKET = 8


def _decided_at(state: TicketState) -> dt.datetime | None:
    swarm = [event for event in state.events if event.agent != "human"]
    done = next((event for event in reversed(swarm) if event.agent == "action" and event.status in ("done", "error")), None)
    return (done or swarm[-1]).timestamp if swarm else None


def metrics() -> dict[str, Any]:
    with Session(engine) as session:
        tickets = session.exec(select(Ticket)).all()
        runs = session.exec(select(TicketRun).order_by(TicketRun.ticket_id, TicketRun.run_number)).all()
        completed_refunds = session.exec(select(DeliveryOperation).where(
            DeliveryOperation.kind == "refund", DeliveryOperation.state == "succeeded"
        )).all()
    status = Counter(_status(t.status) for t in tickets)
    tickets_by_id = {ticket.id: ticket for ticket in tickets}
    decisions: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    cost_by_agent: dict[str, float | None] = {}
    unknown_cost = False
    timeline: list[dict[str, Any]] = []
    durations: list[float] = []
    suspicious = 0

    # Every stored run is a model call cost, including superseded runs. Ticket's
    # compatibility swarm_state mirrors only the latest run, so never add it here.
    for run in runs:
        if not run.agent_state:
            continue
        state = TicketState.model_validate(run.agent_state)
        for usage in state.usage:
            if usage.cost_usd is None:
                unknown_cost = True
                cost_by_agent[usage.agent] = None
            elif usage.agent not in cost_by_agent:
                cost_by_agent[usage.agent] = usage.cost_usd
            elif cost_by_agent[usage.agent] is not None:
                cost_by_agent[usage.agent] = cost_by_agent[usage.agent] + usage.cost_usd

    # Ticket-level outcomes deliberately use only each ticket's selected/current
    # run. A rerun is not another ticket or another completion.
    for ticket in tickets:
        if ticket.legacy_local_only:
            continue
        run = next((candidate for candidate in runs if candidate.id == ticket.current_run_id), None)
        state_data = run.agent_state if run and run.agent_state else ticket.swarm_state
        if not state_data:
            continue
        state = TicketState.model_validate(state_data)
        if not state.action:
            continue
        decisions[state.action.decision] += 1
        rules.update(state.action.rules_fired)
        category = state.triage.category if state.triage else "other"
        categories[category] += 1
        suspicious += bool(state.triage and state.triage.is_suspicious)
        decided_at = _decided_at(state)
        seconds = ((decided_at - state.events[0].timestamp).total_seconds()
                   if decided_at and state.events else None)
        if seconds is not None:
            durations.append(seconds)
        run_cost = state.cost_usd
        timeline.append({"ticket_id": ticket.id, "run_id": ticket.current_run_id,
                         "decided_at": decided_at.isoformat() if decided_at else None,
                         "decision": state.action.decision, "category": category,
                         "seconds": round(seconds, 2) if seconds is not None else None,
                         "cost_usd": round(run_cost, 5) if run_cost is not None else None})

    processed = sum(decisions.values())
    nonlegacy = [ticket for ticket in tickets if not ticket.legacy_local_only]
    automated = sum(ticket.resolution in {"auto_resolved", "auto_refunded"} for ticket in nonlegacy)
    reviews = Counter(run.review_outcome for run in runs if run.review_outcome in {"approved", "edited", "rejected"})
    reviewed = sum(reviews.values())
    verified_refunds = [operation for operation in completed_refunds
                        if operation.ticket_id in tickets_by_id and not tickets_by_id[operation.ticket_id].legacy_local_only]
    total_cost = None if unknown_cost else sum(value or 0.0 for value in cost_by_agent.values())

    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    return {
        "tickets_total": len(tickets), "processed": processed, "by_status": dict(status), "by_decision": dict(decisions),
        "by_category": dict(categories.most_common()), "auto_resolved_rate": rate(automated, processed),
        "escalated_rate": rate(decisions["escalate"], processed),
        "avg_time_to_decision_seconds": round(sum(durations) / len(durations), 2) if durations else None,
        "minutes_per_manual_ticket": MINUTES_PER_MANUAL_TICKET,
        "agent_hours_saved": round(automated * MINUTES_PER_MANUAL_TICKET / 60, 2),
        "total_cost_usd": round(total_cost, 4) if total_cost is not None else None,
        "cost_per_ticket_usd": round(total_cost / processed, 5) if total_cost is not None and processed else None,
        "cost_by_agent": {agent: round(cost, 5) if cost is not None else None for agent, cost in cost_by_agent.items()},
        "escalations_by_rule": dict(rules.most_common()),
        "human_reviews": {key: reviews[key] for key in ("approved", "edited", "rejected")},
        "ai_reply_acceptance_rate": rate(reviews["approved"], reviewed), "suspicious_flagged": suspicious,
        "verified_refund_count": len(verified_refunds),
        "auto_refunded_total": round(sum((op.request or {}).get("amount_minor", 0) / 100 for op in verified_refunds), 2),
        "timeline": sorted(timeline, key=lambda point: point["decided_at"] or ""),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
