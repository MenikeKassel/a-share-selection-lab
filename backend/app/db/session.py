from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base


@lru_cache(maxsize=4)
def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(
        bind=create_database_engine(database_url),
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


def initialize_database(database_url: str | None = None) -> None:
    # Import models so SQLAlchemy registers all metadata before create_all.
    from app.db import models as _models  # noqa: F401

    Base.metadata.create_all(create_database_engine(database_url))


def get_db() -> Generator[Session, None, None]:
    factory = create_session_factory()
    with factory() as session:
        yield session
