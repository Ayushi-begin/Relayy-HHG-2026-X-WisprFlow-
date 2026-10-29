"""Ticket API, durable review mutations, and replayable SSE streams."""

import asyncio
import datetime as dt
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, func, select

import ticket_service
from db import engine
from demo_auth import require_operator
from events import ALL_TICKETS, Message, bus
from models import Customer, Ticket, TicketEvent
from swarm.state import TicketState, utcnow

router = APIRouter()
HEARTBEAT_SECONDS = 15


class TicketCreate(BaseModel):
    subject: str = Field(max_length=200)
    body: str = Field(max_length=10_000)
    customer_email: str | None = None
    customer_id: int | None = None


class HumanReviewRequest(BaseModel):
    action: Literal["approved", "edited", "rejected"]
    reply: str | None = Field(default=None, max_length=10_000)
    reviewer: str | None = None  # label only; actor comes from require_operator
    run_id: str
    expected_version: int = Field(ge=0)


class RecoveryRequest(BaseModel):
    expected_version: int = Field(ge=0)


class TicketAccepted(BaseModel):
    id: int
    status: str
    stream_url: str


class TicketSummary(BaseModel):
    id: int
    subject: str
    status: str
    customer_id: int
    customer_name: str | None
    customer_tier: str | None
    created_at: dt.datetime
    updated_at: dt.datetime | None
    category: str | None
    urgency: str | None
    decision: str | None
    rules_fired: list[str]
    resolution: str | None
    is_suspicious: bool
    run_id: str | None = None
    fulfillment_state: str = "not_requested"
    legacy_local_only: bool = False


class TicketDetail(BaseModel):
    id: int
    status: str
    customer_name: str | None
    customer_email: str | None
    customer_tier: str | None
    resolution: str | None
    resolved_by: str | None
    final_reply: str | None
    running: bool
    state: TicketState
    run_id: str | None = None
    version: int
    execution_mode: str
    fulfillment_state: str
    manual_followup_required: bool
    can_review: bool
    can_reprocess: bool
    operations: list[dict[str, Any]]
    legacy_local_only: bool


def _get_ticket(session: Session, ticket_id: int) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Ticket {ticket_id} not found")
    return ticket


def _detail(session: Session, ticket: Ticket) -> TicketDetail:
    customer = session.get(Customer, ticket.customer_id)
    return TicketDetail(
        **ticket_service.serialize_ticket(session, ticket),
        customer_name=customer.name if customer else None,
        customer_email=customer.email if customer else None,
        customer_tier=str(customer.tier) if customer else None,
    )


def _accepted(ticket_id: int) -> TicketAccepted:
    return TicketAccepted(id=ticket_id, status="processing", stream_url=f"/tickets/{ticket_id}/stream")


@router.post("/tickets", status_code=status.HTTP_202_ACCEPTED)
async def create_ticket(payload: TicketCreate, _: str = Depends(require_operator)) -> TicketAccepted:
    """Create a validated intake and immediately start its first persisted run."""
    subject, body = payload.subject.strip(), payload.body.strip()
    if not subject or not body:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Subject and body cannot be blank")
    if payload.customer_id is None and not (payload.customer_email or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Provide customer_id or customer_email")
    with Session(engine) as session:
        by_id = session.get(Customer, payload.customer_id) if payload.customer_id is not None else None
        by_email = session.exec(select(Customer).where(Customer.email == (payload.customer_email or "").strip().lower())).first() \
            if (payload.customer_email or "").strip() else None
        if payload.customer_id is not None and by_id is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown customer")
        if payload.customer_email and by_email is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown customer")
        if by_id and by_email and by_id.id != by_email.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "customer_id and customer_email disagree")
        customer = by_id or by_email
        assert customer is not None
        ticket = Ticket(customer_id=customer.id, subject=subject, body=body, created_at=utcnow())
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
    if not ticket_service.start_processing(ticket.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket cannot be processed")
    return _accepted(ticket.id)


@router.post("/tickets/{ticket_id}/process", status_code=status.HTTP_202_ACCEPTED)
async def process_ticket(ticket_id: int, _: str = Depends(require_operator)) -> TicketAccepted:
    with Session(engine) as session:
        _get_ticket(session, ticket_id)
    if not ticket_service.start_processing(ticket_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket has active processing, review, or a delivery operation")
    return _accepted(ticket_id)


@router.post("/tickets/{ticket_id}/review", response_model=TicketDetail, status_code=status.HTTP_202_ACCEPTED)
def review_ticket(ticket_id: int, payload: HumanReviewRequest, actor: str = Depends(require_operator)) -> TicketDetail:
    if payload.action == "edited" and not (payload.reply or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Edited replies cannot be blank")
    try:
        _, _, applied = ticket_service.apply_human_action(
            ticket_id, payload.action, actor, run_id=payload.run_id, expected_version=payload.expected_version,
            reply=payload.reply, channel="web",
        )
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Ticket #{ticket_id} not found")
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if not applied:
        raise HTTPException(status.HTTP_409_CONFLICT, "Review is stale or was already handled; refresh the ticket.")
    with Session(engine) as session:
        return _detail(session, _get_ticket(session, ticket_id))


@router.post("/tickets/{ticket_id}/operations/{operation_id}/{action}", response_model=TicketDetail,
             status_code=status.HTTP_202_ACCEPTED)
def recover_operation(ticket_id: int, operation_id: str, action: Literal["retry", "reconcile"], payload: RecoveryRequest,
                      _: str = Depends(require_operator)) -> TicketDetail:
    try:
        ticket_service.request_recovery(ticket_id, operation_id, payload.expected_version, action)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket or operation not found")
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    with Session(engine) as session:
        return _detail(session, _get_ticket(session, ticket_id))


@router.get("/tickets")
def list_tickets() -> list[TicketSummary]:
    with Session(engine) as session:
        tickets = session.exec(select(Ticket).order_by(Ticket.created_at.desc())).all()
        customers = {customer.id: customer for customer in session.exec(select(Customer)).all()}
        results: list[TicketSummary] = []
        for ticket in tickets:
            state = ticket_service.load_state(ticket)
            customer = customers.get(ticket.customer_id)
            results.append(TicketSummary(
                id=ticket.id, subject=ticket.subject, status=str(ticket.status), customer_id=ticket.customer_id,
                customer_name=customer.name if customer else None, customer_tier=str(customer.tier) if customer else None,
                created_at=ticket.created_at, updated_at=ticket.updated_at,
                category=state.triage.category if state.triage else None,
                urgency=state.triage.urgency if state.triage else None,
                decision=state.action.decision if state.action else None,
                rules_fired=state.action.rules_fired if state.action else [], resolution=ticket.resolution,
                is_suspicious=bool(state.triage and state.triage.is_suspicious), run_id=ticket.current_run_id,
                fulfillment_state=ticket.fulfillment_state, legacy_local_only=ticket.legacy_local_only,
            ))
        return results


@router.get("/tickets/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: int) -> TicketDetail:
    with Session(engine) as session:
        return _detail(session, _get_ticket(session, ticket_id))


@router.get("/tickets/{ticket_id}/runs")
def get_runs(ticket_id: int) -> list[dict[str, Any]]:
    with Session(engine) as session:
        _get_ticket(session, ticket_id)
        return ticket_service.run_summaries(session, ticket_id)


def _sse(message: Message) -> str:
    lines = [f"id: {message.id}"] if message.id is not None else []
    lines += [f"event: {message.kind}", f"data: {json.dumps(jsonable_encoder(message.data), separators=(',', ':'))}"]
    return "\n".join(lines) + "\n\n"


def _event_message(event: TicketEvent) -> Message:
    return Message("event", {
        "ticket_id": event.ticket_id, "run_id": event.run_id, "kind": event.kind,
        "timestamp": event.timestamp.isoformat(), "data": event.data,
    }, event.id)


@router.get("/tickets/{ticket_id}/stream")
async def stream_ticket(ticket_id: int, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    """Replay persistent TicketEvent rows, then stream bounded live notifications."""
    # Validate before returning StreamingResponse: errors raised by its generator are
    # otherwise sent after the successful SSE headers and become opaque disconnects.
    with Session(engine) as session:
        _get_ticket(session, ticket_id)
    try:
        cursor = int(last_event_id) if last_event_id is not None else 0
    except ValueError:
        cursor = -1

    async def events():
        # Subscribe first. The durable watermark is captured only afterwards, so a
        # commit that races setup appears either in replay or in this queue.
        with bus.subscribe(ticket_id) as queue:
            with Session(engine) as session:
                ticket = _get_ticket(session, ticket_id)
                watermark = session.exec(select(func.max(TicketEvent.id)).where(TicketEvent.ticket_id == ticket_id)).one() or 0
                if cursor < 0 or cursor > watermark:
                    yield _sse(Message("reset", {"reason": "obsolete_cursor"}))
                    cursor_for_replay = 0
                else:
                    cursor_for_replay = cursor
                snapshot = _detail(session, ticket).model_dump(mode="json")
                yield _sse(Message("snapshot", snapshot))
                history = session.exec(select(TicketEvent).where(
                    TicketEvent.ticket_id == ticket_id, TicketEvent.id > cursor_for_replay, TicketEvent.id <= watermark
                ).order_by(TicketEvent.id)).all()
            for event in history:
                yield _sse(_event_message(event))
            # TicketEvent IDs are globally monotonic, so this scalar deduplicates
            # replay/subscribe overlap without retaining an unbounded set.
            last_sent = watermark
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if message.kind == "reset":
                    yield _sse(message)
                    return
                if message.id is not None:
                    if message.id <= last_sent:
                        continue
                    last_sent = message.id
                yield _sse(message)

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@router.get("/events/stream")
async def stream_all() -> StreamingResponse:
    async def events():
        with bus.subscribe(ALL_TICKETS) as queue:
            yield ": connected\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if message.kind == "reset":
                    return
                yield _sse(message)
    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@router.get("/metrics")
def get_metrics() -> dict[str, Any]:
    return ticket_service.metrics()
