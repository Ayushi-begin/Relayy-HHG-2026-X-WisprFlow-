from collections.abc import Iterator

from sqlalchemy import event, text
from sqlalchemy.orm import Session as OrmSession
from sqlmodel import Session, SQLModel, create_engine

import models  # noqa: F401  (registers tables on SQLModel.metadata)
from config import DATABASE_URL, MIGRATION_DATABASE_URL

# Arbitrary application-wide key for the event-ordering advisory lock.
EVENT_ORDER_LOCK = 72_011


def normalize_url(url: str) -> str:
    """Use the psycopg 3 driver for Postgres URLs as Neon and Render print them."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _engine(url: str):
    url = normalize_url(url)
    if url.startswith("sqlite:"):
        # check_same_thread=False lets FastAPI's threadpool share the SQLite connection.
        return create_engine(url, connect_args={"check_same_thread": False})
    # Neon closes idle connections and may scale to zero: verify connections before use and
    # recycle them well inside its idle timeout. Prefer Neon's pooled (-pooler) host in production.
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, pool_recycle=300)


engine = _engine(DATABASE_URL)


@event.listens_for(OrmSession, "before_flush")
def _serialize_event_inserts(session: OrmSession, _context, _instances) -> None:
    """Make TicketEvent ids commit in id order on Postgres.

    SSE replay uses the max committed event id as a watermark and deduplicates live messages by
    id, which assumes ids become visible in increasing order. SQLite serializes writers, so that
    holds there; on Postgres a transaction-scoped advisory lock taken before inserting events
    gives the same guarantee. The lock is released at commit or rollback.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    if any(isinstance(obj, models.TicketEvent) for obj in session.new):
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": EVENT_ORDER_LOCK})


def create_db_and_tables() -> None:
    from migrations import migrate

    # Neon recommends a direct (non-pooled) connection for schema changes; use it when provided.
    if MIGRATION_DATABASE_URL:
        migration_engine = _engine(MIGRATION_DATABASE_URL)
        try:
            migrate(migration_engine)
        finally:
            migration_engine.dispose()
    else:
        migrate(engine)


def ping() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
