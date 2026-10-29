from collections.abc import Iterator

from sqlalchemy import inspect
from sqlmodel import Session, SQLModel, create_engine

import models  # noqa: F401  (registers tables on SQLModel.metadata)
from config import DATABASE_URL

# check_same_thread=False lets FastAPI's threadpool share the SQLite connection.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Minimal migration: add model columns missing from existing tables.

    create_all() only creates new tables, so columns added to a model later would be
    missing from an existing relayy.db. New columns must be nullable.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing:
                    column_type = column.type.compile(engine.dialect)
                    conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}')


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
