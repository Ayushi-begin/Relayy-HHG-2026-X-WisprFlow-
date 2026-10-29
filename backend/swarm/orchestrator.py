"""Runs triage -> research -> action for one ticket, streaming progress events."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from swarm import llm
from swarm.action import act
from swarm.research import research
from swarm.state import AgentName, Event, EventStatus, TicketData, TicketState
from swarm.triage import triage

Emit = Callable[[str, str], None]  # (status, message), bound to the running agent


async def _run_triage(state: TicketState, emit: Emit) -> str:
    state.triage = t = await triage(state.ticket, state.usage, state.provenance, state.provenance_errors)
    flag = " (suspicious)" if t.is_suspicious else ""
    return f"{t.category} / {t.urgency} urgency / {t.sentiment}{flag}: {t.summary}"


async def _run_research(state: TicketState, emit: Emit) -> str:
    state.research = r = await research(state, emit)
    who = f"{r.customer.name} ({r.customer.tier})" if r.customer else "unknown customer"
    return (
        f"{who}: {len(r.payments)} payments, {len(r.past_tickets)} past tickets, "
        f"{len(r.relevant_docs)} docs, {len(r.key_findings)} findings"
    )


async def _run_action(state: TicketState, emit: Emit) -> str:
    state.action = a = await act(state, emit)
    refund = f" ${a.refund_amount:.2f}" if a.refund_amount else ""
    return f"{a.decision}{refund} (confidence {a.confidence:.2f})"


STEPS: list[tuple[AgentName, str, Callable[[TicketState, Emit], Awaitable[str]]]] = [
    ("triage", "Classifying ticket", _run_triage),
    ("research", "Gathering customer context", _run_research),
    ("action", "Deciding on a resolution", _run_action),
]


async def run_pipeline(state: TicketState) -> AsyncIterator[Event]:
    """Run every agent in order, yielding each event as soon as it is appended to state.events.

    Agents can emit their own events mid-step (tool calls, policy overrides). The caller keeps
    the `state` it passed in; afterwards it holds the triage, research and action results.
    If a step fails, an error event is yielded and the remaining steps are skipped.
    """
    queue: asyncio.Queue[Event | None] = asyncio.Queue()

    def emit(agent: AgentName, status: EventStatus, message: str) -> None:
        event = Event(agent=agent, status=status, message=message)
        state.events.append(event)
        queue.put_nowait(event)

    async def drive() -> None:
        try:
            emit("orchestrator", "running", f"Processing ticket: {state.ticket.subject}")
            for agent, label, step in STEPS:
                emit(agent, "running", f"{label}...")
                try:
                    summary = await step(state, lambda status, message, agent=agent: emit(agent, status, message))
                except Exception as exc:  # surface agent failures as events instead of crashing the stream
                    error = llm.sanitized_error(exc)
                    llm.record_provenance(state.provenance, state.provenance_errors, agent, "failed", error)
                    emit(agent, "error", error)
                    emit("orchestrator", "error", f"Stopped after {agent} failed.")
                    return
                emit(agent, "done", summary)
            emit("orchestrator", "done", "All agents finished.")
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(drive())
    try:
        while (event := await queue.get()) is not None:
            yield event
        await task
    finally:
        task.cancel()  # no-op if finished; stops the agents if the consumer disconnects early


async def process_ticket(ticket: TicketData) -> TicketState:
    """Run the full pipeline without streaming and return the final state."""
    state = TicketState(ticket=ticket)
    async for _ in run_pipeline(state):
        pass
    return state
