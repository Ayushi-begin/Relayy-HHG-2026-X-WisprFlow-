"""Short, conditional database claims. No network I/O belongs in this module."""

import datetime as dt
import hashlib
import json
import uuid

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, select

from models import DeliveryOperation, OperationAttempt


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def aware(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def fingerprint(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def freeze(session: Session, *, ticket_id: int, run_id: str, kind: str, request: dict,
           logical_key: str, dependency_id: str | None = None, blocked: str | None = None) -> DeliveryOperation:
    identifier = str(uuid.uuid4())
    operation = DeliveryOperation(
        id=identifier, ticket_id=ticket_id, run_id=run_id, kind=kind,
        provider="stripe" if kind == "refund" else "resend", account_id=request.get("account_id", ""),
        logical_key=logical_key, request=request, request_hash=fingerprint(request),
        idempotency_key=f"relayy-{identifier}", dependency_id=dependency_id,
        state="blocked" if blocked else "queued", error_code=blocked,
        error_message="Delivery is blocked. Check test configuration and authorization." if blocked else None,
        created_at=now(), updated_at=now(),
    )
    # The uniqueness constraint is the final arbiter across simultaneous tickets.
    session.exec(insert(DeliveryOperation).values(**operation.model_dump()).on_conflict_do_nothing())
    saved = session.exec(select(DeliveryOperation).where(DeliveryOperation.logical_key == logical_key)).one()
    if saved.ticket_id != ticket_id or saved.run_id != run_id or saved.request_hash != operation.request_hash:
        raise ValueError("This payment or ticket already has a different frozen operation.")
    return saved


def claim(session: Session, operation: DeliveryOperation, kind: str) -> tuple[str, OperationAttempt] | None:
    token, timestamp = str(uuid.uuid4()), now()
    first = operation.first_attempt_at or timestamp
    result = session.exec(update(DeliveryOperation).where(
        DeliveryOperation.id == operation.id, DeliveryOperation.version == operation.version,
        DeliveryOperation.state == operation.state,
    ).values(state="executing", claim_token=token, claim_expires_at=timestamp + dt.timedelta(seconds=90),
             first_attempt_at=first, retry_deadline=operation.retry_deadline or aware(first) + dt.timedelta(hours=23),
             attempt_count=operation.attempt_count + 1, version=operation.version + 1, updated_at=timestamp))
    if result.rowcount != 1:
        return None
    attempt = OperationAttempt(operation_id=operation.id, attempt_number=operation.attempt_count,
                               claim_token=token, kind=kind, started_at=timestamp,
                               request_hash=operation.request_hash)
    # SQLAlchemy synchronizes the loaded operation on update; use the refreshed count.
    session.refresh(operation)
    attempt.attempt_number = operation.attempt_count
    session.add(attempt)
    session.commit()
    return token, attempt
