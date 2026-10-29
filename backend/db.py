from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

import models  # noqa: F401  (registers tables on SQLModel.metadata)
from config import DATABASE_URL

# check_same_thread=False lets FastAPI's threadpool share the SQLite connection.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite:") else {})


def create_db_and_tables() -> None:
    from migrations import migrate

    migrate(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
