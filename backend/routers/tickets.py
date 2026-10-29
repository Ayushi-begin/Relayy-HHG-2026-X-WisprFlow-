import asyncio
import datetime as dt
import json

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

import ticket_service
from db import engine
from events import ALL_TICKETS, Message, bus
from models import Customer, Ticket
from swarm.state import TicketState, utcnow

router = APIRouter()

HEARTBEAT_SECONDS = 15


class TicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)
    customer_email: str | None = None
    customer_id: int | None = None


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


def _get_ticket(session: Session, ticket_id: int) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Ticket {ticket_id} not found")
    return ticket


def _accepted(ticket_id: int) -> TicketAccepted:
    return TicketAccepted(id=ticket_id, status="processing", stream_url=f"/tickets/{ticket_id}/stream")


@router.post("/tickets", status_code=status.HTTP_202_ACCEPTED)
async def create_ticket(payload: TicketCreate) -> TicketAccepted:
    """Create a ticket and start the swarm on it in the background."""
    with Session(engine) as session:
        if payload.customer_id is not None:
            customer = session.get(Customer, payload.customer_id)
        elif payload.customer_email:
            customer = session.exec(
                select(Customer).where(Customer.email == payload.customer_email.strip().lower())
            ).first()
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Provide customer_id or customer_email")
        if customer is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown customer")

        ticket = Ticket(customer_id=customer.id, subject=payload.subject, body=payload.body, created_at=utcnow())
        session.add(ticket)
        session.commit()
        session.refresh(ticket)

    ticket_service.start_processing(ticket.id)
    return _accepted(ticket.id)


@router.post("/tickets/{ticket_id}/process", status_code=status.HTTP_202_ACCEPTED)
async def process_ticket(ticket_id: int) -> TicketAccepted:
    """(Re)run the swarm on an existing ticket, e.g. one of the seeded demo tickets."""
    with Session(engine) as session:
        _get_ticket(session, ticket_id)
    if not ticket_service.start_processing(ticket_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket is already being processed")
    return _accepted(ticket_id)


@router.get("/tickets")
def list_tickets() -> list[TicketSummary]:
    with Session(engine) as session:
        tickets = session.exec(select(Ticket).order_by(Ticket.created_at.desc())).all()
        customers = {c.id: c for c in session.exec(select(Customer)).all()}

        summaries = []
        for t in tickets:
            state = ticket_service.load_state(t)
            customer = customers.get(t.customer_id)
            summaries.append(TicketSummary(
                id=t.id,
                subject=t.subject,
                status=t.status,
                customer_id=t.customer_id,
                customer_name=customer.name if customer else None,
                customer_tier=customer.tier if customer else None,
                created_at=t.created_at,
                updated_at=t.updated_at,
                category=state.triage.category if state.triage else None,
                urgency=state.triage.urgency if state.triage else None,
                decision=state.action.decision if state.action else None,
                rules_fired=state.action.rules_fired if state.action else [],
                resolution=t.resolution,
                is_suspicious=bool(state.triage and state.triage.is_suspicious),
            ))
    return summaries


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int) -> TicketDetail:
    with Session(engine) as session:
        t = _get_ticket(session, ticket_id)
        customer = session.get(Customer, t.customer_id)
        return TicketDetail(
            id=t.id,
            status=t.status,
            customer_name=customer.name if customer else None,
            customer_email=customer.email if customer else None,
            customer_tier=customer.tier if customer else None,
            resolution=t.resolution,
            resolved_by=t.resolved_by,
            final_reply=t.final_reply,
            running=ticket_service.is_running(t.id),
            state=ticket_service.load_state(t),
        )


def _sse(message: Message) -> str:
    lines = [f"id: {message.id}"] if message.id is not None else []
    lines += [f"event: {message.kind}", f"data: {json.dumps(message.data)}"]
    return "\n".join(lines) + "\n\n"


@router.get("/tickets/{ticket_id}/stream")
async def stream_ticket(ticket_id: int, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    """Server-Sent Events: the ticket's event history, then live events as they happen.

    `event: event` messages carry one swarm.state.Event (their `id` is its index, so a
    reconnecting EventSource resumes via Last-Event-ID). `event: status` messages carry
    the ticket's status. The stream stays open (with heartbeats) so human decisions made
    in Slack arrive too; the client closes it when done.
    """
    with Session(engine) as session:
        _get_ticket(session, ticket_id)

    try:
        start = int(last_event_id) + 1 if last_event_id else 0
    except ValueError:
        start = 0

    async def events():
        with bus.subscribe(ticket_id) as queue:
            # Subscribing and snapshotting happen with no await in between, so nothing is missed.
            with Session(engine) as session:
                ticket = session.get(Ticket, ticket_id)
                history = ticket_service.load_state(ticket).events
                current = {"status": ticket.status.value, "resolution": ticket.resolution}

            next_id = max(start, 0)
            for index in range(next_id, len(history)):
                yield _sse(Message("event", history[index].model_dump(mode="json"), index))
            next_id = max(next_id, len(history))
            yield _sse(Message("status", current))

            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if message.kind == "status" and message.data["status"] == "processing":
                    next_id = 0  # a re-run starts a fresh event list
                elif message.id is not None:
                    if message.id < next_id:
                        continue  # already sent as part of the history
                    next_id = message.id + 1
                yield _sse(message)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/events/stream")
async def stream_all() -> StreamingResponse:
    """Server-Sent Events: `event: status` ({ticket_id, status, resolution}) whenever any
    ticket is created or changes status. Lets dashboards refresh lists and metrics live."""

    async def events():
        with bus.subscribe(ALL_TICKETS) as queue:
            yield ": connected\n\n"
            while True:
                try:
                    yield _sse(await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS))
                except TimeoutError:
                    yield ": heartbeat\n\n"

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.get("/metrics")
def get_metrics() -> dict:
    return ticket_service.metrics()
