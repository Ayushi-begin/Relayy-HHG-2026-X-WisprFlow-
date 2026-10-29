"""Bounded, event-loop-safe fan-out for persisted ticket events.

TicketEvent rows are the replay source of truth.  This bus only wakes connected SSE
clients after the transaction that created an event has committed.
"""

import asyncio
import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class Message:
    kind: Literal["event", "status", "snapshot", "reset"]
    data: dict[str, Any]
    id: int | None = None


ALL_TICKETS = 0
QUEUE_SIZE = 128


class EventBus:
    """A small single-process notification bus.

    Subscribers remember their owning loop so sync SQLModel workers can publish without
    touching an asyncio.Queue from another thread.  Slow consumers are invalidated and
    must reconnect to replay the durable cursor rather than accumulating memory.
    """

    def __init__(self) -> None:
        self._subscribers: dict[int, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[Message]]]] = defaultdict(set)
        self._lock = threading.Lock()

    def publish_ticket_event(self, ticket_id: int, event_id: int, data: dict[str, Any]) -> None:
        payload = {"ticket_id": ticket_id, **data}
        self._publish(ticket_id, Message("event", payload, event_id))

    def publish_status(
        self, ticket_id: int, status: str, resolution: str | None = None, *, run_id: str | None = None,
        version: int | None = None,
    ) -> None:
        data = {"ticket_id": ticket_id, "status": str(status), "resolution": resolution, "run_id": run_id,
                "version": version}
        message = Message("status", data)
        self._publish(ticket_id, message)
        self._publish(ALL_TICKETS, message)

    def _publish(self, ticket_id: int, message: Message) -> None:
        with self._lock:
            subscribers = tuple(self._subscribers.get(ticket_id, ()))
        for loop, queue in subscribers:
            if loop.is_closed():
                self._discard(ticket_id, (loop, queue))
                continue
            loop.call_soon_threadsafe(self._offer, ticket_id, loop, queue, message)

    def _offer(
        self, ticket_id: int, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[Message], message: Message
    ) -> None:
        if queue.full():
            # Do not drop an arbitrary event and let the client advance its cursor. A
            # reset tells the SSE generator to close; reconnect starts with a snapshot.
            self._discard(ticket_id, (loop, queue))
            try:
                queue.get_nowait()
                queue.put_nowait(Message("reset", {"reason": "subscriber_overflow"}))
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
            return
        queue.put_nowait(message)

    def _discard(self, ticket_id: int, subscriber: tuple[asyncio.AbstractEventLoop, asyncio.Queue[Message]]) -> None:
        with self._lock:
            subscribers = self._subscribers.get(ticket_id)
            if subscribers:
                subscribers.discard(subscriber)
                if not subscribers:
                    self._subscribers.pop(ticket_id, None)

    @contextmanager
    def subscribe(self, ticket_id: int) -> Iterator[asyncio.Queue[Message]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=QUEUE_SIZE)
        subscriber = (loop, queue)
        with self._lock:
            self._subscribers[ticket_id].add(subscriber)
        try:
            yield queue
        finally:
            self._discard(ticket_id, subscriber)


bus = EventBus()
