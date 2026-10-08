from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from packages.config import Settings, get_settings


def get_engine(settings: Settings | None = None) -> Engine:
    resolved = settings or get_settings()
    database_url = resolved.resolve_database_url()
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine_kwargs: dict[str, object] = {
        "future": True,
        "connect_args": connect_args,
    }

    if not database_url.startswith("sqlite"):
        engine_kwargs.update(
            {
                "pool_pre_ping": True,
                "pool_recycle": 300,
                "pool_use_lifo": True,
            }
        )
        if resolved.is_staging:
            engine_kwargs.update({"pool_size": 1, "max_overflow": 0})

    return create_engine(database_url, **engine_kwargs)


def create_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    engine = get_engine(settings)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def session_scope(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
