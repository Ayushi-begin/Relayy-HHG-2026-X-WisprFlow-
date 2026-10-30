"""Append-only audit trail for operator mutations."""

from typing import Any

from sqlmodel import Session, select

from db import engine
from models import AuditLog
from swarm.state import utcnow


def record(session: Session, *, actor: str, action: str, target: str, detail: dict[str, Any] | None = None) -> None:
    """Add an audit row to the caller's transaction, so it commits with the change it describes."""
    session.add(AuditLog(at=utcnow(), actor=actor, action=action, target=target, detail=detail or {}))


def record_now(*, actor: str, action: str, target: str, detail: dict[str, Any] | None = None) -> None:
    """Record an action whose own state change was committed elsewhere (for example the run service)."""
    with Session(engine) as session:
        record(session, actor=actor, action=action, target=target, detail=detail)
        session.commit()


def recent(limit: int = 100) -> list[dict[str, Any]]:
    with Session(engine) as session:
        rows = session.exec(select(AuditLog).order_by(AuditLog.id.desc()).limit(max(1, min(limit, 500)))).all()
        return [row.model_dump(mode="json") for row in rows]
