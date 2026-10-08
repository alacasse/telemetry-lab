from __future__ import annotations

from functools import lru_cache
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from packages.aws import configure_tracing
from packages.config import Settings, get_settings
from packages.db.session import create_session_factory
from packages.logging import configure_logging
from processing_worker.processor import process_sqs_event

settings = get_settings()
settings.require_staging_runtime_env_vars("APP_ENV", "DATABASE_SECRET_ARN", "QUEUE_BACKEND")
configure_tracing("processing-worker", settings)
configure_logging("processing-worker", settings.log_level)


@lru_cache(maxsize=1)
def _get_cached_session_factory() -> sessionmaker[Session]:
    return create_session_factory(get_settings())


def process_event(
    event: dict[str, Any],
    *,
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> dict[str, list[dict[str, str]]]:
    resolved_settings = settings or get_settings()
    resolved_session_factory = session_factory or _get_cached_session_factory()
    return process_sqs_event(event, resolved_session_factory, resolved_settings)


def handler(event: dict[str, Any], context: Any) -> dict[str, list[dict[str, str]]]:
    del context
    return process_event(event)
