"""Postgres-only behavior. Runs when TEST_POSTGRES_URL is set (CI provides a Postgres service).

Each test uses its own throwaway schema so it never touches existing data.
"""

import datetime as dt
import os
import uuid

import pytest
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine, select

import db
from migrations import CURRENT_VERSION, migrate
from models import Customer, DeliveryOperation, Ticket, TicketEvent

URL = os.getenv("TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL is not set")


@pytest.fixture
def engine():
    schema = f"relayy_test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(db.normalize_url(URL))
    with admin.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped = create_engine(db.normalize_url(URL), connect_args={"options": f"-csearch_path={schema}"})
    try:
        yield scoped
    finally:
        scoped.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def test_url_normalization_selects_psycopg():
    assert db.normalize_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert db.normalize_url("postgresql://u:p@h/db?sslmode=require").startswith("postgresql+psycopg://")


def test_migration_is_idempotent_and_versioned(engine):
    migrate(engine)
    migrate(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT MAX(version) FROM schema_version").scalar() == CURRENT_VERSION
    indexes = {index["name"] for index in inspect(engine).get_indexes("ticket_event")}
    assert "ix_ticket_event_ticket_id_id" in indexes
    assert "payment_provider_charge" in {index["name"] for index in inspect(engine).get_indexes("payment")}


def test_migration_refuses_unversioned_relayy_tables(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE customer (id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="refusing to guess"):
        migrate(engine)


def _customer_and_ticket(session: Session) -> Ticket:
    session.add(Customer(id=1, name="Test", email="t@example.com", plan="Pro", tier="pro",
                         signup_date=dt.date(2026, 1, 1), lifetime_value=10))
    ticket = Ticket(customer_id=1, subject="s", body="b", created_at=dt.datetime.now(dt.timezone.utc), status="open")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def test_frozen_operation_conflicts_are_resolved_by_the_unique_key(engine, monkeypatch):
    from fulfillment import store

    migrate(engine)
    with Session(engine) as session:
        ticket = _customer_and_ticket(session)
        first = store.freeze(session, ticket_id=ticket.id, run_id="run", kind="email", request={"a": 1},
                             logical_key="email:1")
        again = store.freeze(session, ticket_id=ticket.id, run_id="run", kind="email", request={"a": 1},
                             logical_key="email:1")
        session.commit()
        assert first.id == again.id
        with pytest.raises(ValueError):
            store.freeze(session, ticket_id=ticket.id, run_id="other", kind="email", request={"a": 2},
                         logical_key="email:1")
        assert len(session.exec(select(DeliveryOperation)).all()) == 1


def test_event_inserts_take_the_ordering_lock(engine):
    migrate(engine)
    with Session(engine) as session:
        ticket = _customer_and_ticket(session)
        session.add(TicketEvent(ticket_id=ticket.id, kind="status", timestamp=dt.datetime.now(dt.timezone.utc), data={}))
        session.flush()
        held = session.execute(text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = :key AND pid = pg_backend_pid()"
        ), {"key": db.EVENT_ORDER_LOCK}).scalar()
        assert held == 1
        session.commit()
        released = session.execute(text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = :key AND pid = pg_backend_pid()"
        ), {"key": db.EVENT_ORDER_LOCK}).scalar()
        assert released == 0


def test_seed_advances_sequences_and_refuses_nonempty_remote(engine, monkeypatch):
    import seed

    monkeypatch.setattr(seed, "engine", engine)
    monkeypatch.setattr(seed, "create_db_and_tables", lambda: migrate(engine))
    seed.seed()
    with Session(engine) as session:
        highest = session.exec(select(Ticket.id).order_by(Ticket.id.desc())).first()
        new = Ticket(customer_id=1, subject="new", body="b", created_at=dt.datetime.now(dt.timezone.utc), status="open")
        session.add(new)
        session.commit()
        assert new.id > highest
    with pytest.raises(RuntimeError, match="already contains customers"):
        seed.seed()
    with pytest.raises(RuntimeError, match="RELAYY_ALLOW_REMOTE_RESET"):
        seed.seed(reset=True)
