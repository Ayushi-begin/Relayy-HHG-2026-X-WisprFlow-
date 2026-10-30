import pytest
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from migrations import migrate
from models import DeliveryOperation, Ticket


def test_legacy_snapshot_gets_nullable_bindings_and_truthful_provenance():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE customer (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, email VARCHAR NOT NULL, plan VARCHAR NOT NULL, tier VARCHAR NOT NULL, signup_date DATE NOT NULL, lifetime_value FLOAT NOT NULL)")
        conn.exec_driver_sql("CREATE TABLE payment (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, amount FLOAT NOT NULL, date DATETIME NOT NULL, status VARCHAR NOT NULL, description VARCHAR NOT NULL)")
        conn.exec_driver_sql("CREATE TABLE ticket (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, subject VARCHAR NOT NULL, body VARCHAR NOT NULL, created_at DATETIME NOT NULL, status VARCHAR NOT NULL, swarm_state JSON, resolution VARCHAR, resolved_by VARCHAR, final_reply VARCHAR)")
        conn.exec_driver_sql("INSERT INTO ticket VALUES (1, 1, 'Old', 'Body', '2026-10-28 10:00:00', 'resolved', NULL, 'auto_refunded', 'ai', 'Local only')")
    migrate(engine)
    migrate(engine)
    with Session(engine) as session:
        ticket = session.get(Ticket, 1)
        assert ticket.legacy_local_only is True
        assert ticket.fulfillment_state == "legacy_local_only"
        assert ticket.current_run_id is None
        assert ticket.final_reply == "Local only"
        assert session.exec(select(DeliveryOperation)).first() is None
    assert "payment_provider_charge" in {index["name"] for index in inspect(engine).get_indexes("payment")}


def test_seed_refuses_reset_after_external_operation(monkeypatch):
    import seed
    engine = create_engine("sqlite://", poolclass=StaticPool)
    monkeypatch.setattr(seed, "engine", engine)
    monkeypatch.setattr(seed, "create_db_and_tables", lambda: migrate(engine))
    migrate(engine)
    from fulfillment.store import now
    with Session(engine) as session:
        session.add(DeliveryOperation(id="op", ticket_id=1, run_id="run", kind="email", provider="resend", logical_key="email:1",
                                      request={}, request_hash="hash", idempotency_key="key", created_at=now(), updated_at=now()))
        session.commit()
    with pytest.raises(RuntimeError, match="Refuse reseeding"):
        seed.seed(reset=True)
    with Session(engine) as session:
        assert session.get(DeliveryOperation, "op") is not None


def test_version_two_adds_independent_budgets_without_changing_operations():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    migrate(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM schema_version WHERE version>=2")
        for column in ("send_attempt_count", "check_attempt_count", "manual_check_requested"):
            conn.exec_driver_sql(f"ALTER TABLE delivery_operation DROP COLUMN {column}")
        conn.exec_driver_sql("INSERT INTO delivery_operation (id,ticket_id,run_id,kind,provider,account_id,logical_key,request,request_hash,idempotency_key,state,attempt_count,version,created_at,updated_at) VALUES ('old',1,'run','refund','stripe','','refund:old','{}','hash','key','succeeded',6,3,'2026-10-29','2026-10-29')")
    migrate(engine)
    with Session(engine) as session:
        op = session.get(DeliveryOperation, "old")
        assert op.state == "succeeded" and op.attempt_count == 6 and op.version == 3
        assert op.send_attempt_count == 6 and op.check_attempt_count == 0
        assert not op.manual_check_requested
