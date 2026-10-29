"""In-process pub/sub that fans ticket updates out to live SSE streams.

Messages are not stored here: the event history lives on the ticket's TicketState
(in memory while the swarm runs, then in the DB). A single-process deployment is
assumed, which is what `uvicorn main:app` gives you.
"""

import asyncio
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from swarm.state import Event


@dataclass
class Message:
    kind: Literal["event", "status"]
    data: dict[str, Any]
    id: int | None = None  # index into the ticket's event list, for "event" messages


ALL_TICKETS = 0  # subscription key for status changes on every ticket


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue[Message]]] = defaultdict(set)

    def publish_event(self, ticket_id: int, index: int, event: Event) -> None:
        self._publish(ticket_id, Message("event", event.model_dump(mode="json"), index))

    def publish_status(self, ticket_id: int, status: str, resolution: str | None = None) -> None:
        data = {"ticket_id": ticket_id, "status": status, "resolution": resolution}
        self._publish(ticket_id, Message("status", data))
        self._publish(ALL_TICKETS, Message("status", data))

    def _publish(self, ticket_id: int, message: Message) -> None:
        for queue in self._subscribers.get(ticket_id, ()):
            queue.put_nowait(message)

    @contextmanager
    def subscribe(self, ticket_id: int) -> Iterator[asyncio.Queue[Message]]:
        queue: asyncio.Queue[Message] = asyncio.Queue()
        self._subscribers[ticket_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[ticket_id].discard(queue)
            if not self._subscribers[ticket_id]:
                del self._subscribers[ticket_id]


bus = EventBus()
