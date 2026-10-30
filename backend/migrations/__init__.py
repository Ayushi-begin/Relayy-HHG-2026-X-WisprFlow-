"""Versioned, additive schema migrations for SQLite (local) and Postgres (Neon in production).

Migrations only create tables, add columns and add indexes. They never drop or rewrite data,
and they refuse to guess when a database holds Relayy tables without a recorded version.
"""

from sqlalchemy import inspect
from sqlmodel import SQLModel

CURRENT_VERSION = 4  # 4: agent_setting and audit_log tables (created by create_all)
MIGRATION_LOCK = 72_012  # Postgres advisory lock: one migrator at a time across instances

# Version 3: indexes for the inbox, the worker poll and SSE replay.
_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS payment_provider_charge "
    "ON payment(provider_account_id, stripe_charge_id) WHERE stripe_charge_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_ticket_created_at ON ticket(created_at)",
    "CREATE INDEX IF NOT EXISTS ix_ticket_status ON ticket(status)",
    "CREATE INDEX IF NOT EXISTS ix_ticket_event_ticket_id_id ON ticket_event(ticket_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_delivery_operation_state ON delivery_operation(state)",
    "CREATE INDEX IF NOT EXISTS ix_ticket_run_state ON ticket_run(state)",
)


def migrate(engine) -> None:
    if engine.dialect.name == "sqlite":
        _migrate_sqlite(engine)
    elif engine.dialect.name == "postgresql":
        _migrate_postgres(engine)
    else:
        raise RuntimeError(f"Unsupported database dialect: {engine.dialect.name}")


def _version(conn) -> int:
    conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
    return conn.exec_driver_sql("SELECT MAX(version) FROM schema_version").scalar() or 0


def _record(conn, version: int) -> None:
    conn.exec_driver_sql(f"INSERT INTO schema_version(version) VALUES ({int(version)})")


def _migrate_postgres(engine) -> None:
    # Postgres DDL is transactional: a failed migration leaves the schema unchanged.
    with engine.begin() as conn:
        conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({MIGRATION_LOCK})")
        version = _version(conn)
        if version > CURRENT_VERSION:
            raise RuntimeError("Database schema is newer than this application.")
        if version == 0:
            existing = set(inspect(conn).get_table_names()) & set(SQLModel.metadata.tables)
            if existing:
                raise RuntimeError(
                    "Postgres database has Relayy tables but no schema version; refusing to guess. "
                    "Inspect it manually before migrating."
                )
        SQLModel.metadata.create_all(conn)
        for statement in _INDEXES:
            conn.exec_driver_sql(statement)
        if version < CURRENT_VERSION:
            _record(conn, CURRENT_VERSION)


def _migrate_sqlite(engine) -> None:
    # SQLite DDL participates in this explicit transaction. Never infer provider success.
    with engine.begin() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        version = _version(conn)
        if version > CURRENT_VERSION:
            raise RuntimeError("Database schema is newer than this application.")
        SQLModel.metadata.create_all(conn)
        if version >= 2:
            # Indexes are idempotent; re-applying them also repairs tables recreated by a reset.
            for statement in _INDEXES:
                conn.exec_driver_sql(statement)
            if version < CURRENT_VERSION:
                _record(conn, CURRENT_VERSION)
            return
        inspector = inspect(conn)
        defaults = {"version": "0", "legacy_local_only": "0", "manual_followup_required": "0",
                    "fulfillment_state": "'not_requested'", "send_attempt_count": "0",
                    "check_attempt_count": "0", "manual_check_requested": "0"}
        for table in SQLModel.metadata.sorted_tables:
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                suffix = f" DEFAULT {defaults[column.name]}" if column.name in defaults else ""
                conn.exec_driver_sql(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" '
                    f'{column.type.compile(engine.dialect)}{suffix}'
                )
        for statement in _INDEXES:
            conn.exec_driver_sql(statement)
        if version < 1:
            conn.exec_driver_sql(
                "UPDATE ticket SET legacy_local_only=1, fulfillment_state='legacy_local_only' "
                "WHERE current_run_id IS NULL AND (resolution IS NOT NULL OR final_reply IS NOT NULL)"
            )
            _record(conn, 1)
        elif version == 1:
            # Older attempts did not distinguish reads from submissions reliably.
            # Count them conservatively against sending, never grant a fresh budget.
            conn.exec_driver_sql("UPDATE delivery_operation SET send_attempt_count=attempt_count")
        _record(conn, 2)
        _record(conn, CURRENT_VERSION)
