"""Versioned migration for the intentionally SQLite-only hackathon runtime."""

from sqlalchemy import inspect
from sqlmodel import SQLModel


def migrate(engine) -> None:
    if engine.dialect.name != "sqlite":
        raise RuntimeError("This demo supports SQLite only; migrate explicitly before changing databases.")
    # SQLite DDL participates in this explicit transaction. Never infer provider success.
    with engine.begin() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
        version = conn.exec_driver_sql("SELECT MAX(version) FROM schema_version").scalar() or 0
        if version > 2:
            raise RuntimeError("Database schema is newer than this application.")
        if version == 2:
            SQLModel.metadata.create_all(conn)
            return
        SQLModel.metadata.create_all(conn)
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
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS payment_provider_charge "
            "ON payment(provider_account_id, stripe_charge_id) WHERE stripe_charge_id IS NOT NULL"
        )
        if version < 1:
            conn.exec_driver_sql(
                "UPDATE ticket SET legacy_local_only=1, fulfillment_state='legacy_local_only' "
                "WHERE current_run_id IS NULL AND (resolution IS NOT NULL OR final_reply IS NOT NULL)"
            )
            conn.exec_driver_sql("INSERT INTO schema_version(version) VALUES (1)")
        elif version == 1:
            # Older attempts did not distinguish reads from submissions reliably.
            # Count them conservatively against sending, never grant a fresh budget.
            conn.exec_driver_sql("UPDATE delivery_operation SET send_attempt_count=attempt_count")
        conn.exec_driver_sql("INSERT INTO schema_version(version) VALUES (2)")
