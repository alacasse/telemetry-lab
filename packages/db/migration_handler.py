from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from packages.aws import configure_tracing
from packages.config import get_settings
from packages.logging import configure_logging, get_logger

ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = ROOT / "alembic.ini"

settings = get_settings()
settings.require_staging_runtime_env_vars("APP_ENV", "DATABASE_SECRET_ARN", "QUEUE_BACKEND")
configure_tracing("migration-handler", settings)
configure_logging("migration-handler", settings.log_level)
logger = get_logger("migration-handler")


def get_alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    url = database_url or get_settings().resolve_database_url()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def get_current_revision(config: Config) -> str | None:
    database_url = config.get_main_option("sqlalchemy.url")
    if not database_url:
        raise RuntimeError("Alembic configuration is missing sqlalchemy.url")
    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            heads = sorted(context.get_current_heads())
    finally:
        engine.dispose()

    if not heads:
        return None
    return ",".join(heads)


def get_expected_head_revision(config: Config) -> str:
    current_head = ScriptDirectory.from_config(config).get_current_head()
    if current_head is None:
        raise RuntimeError("Alembic script directory has no head revision")
    return current_head


def resolve_revision(config: Config, revision: str) -> str:
    if revision == "head":
        return get_expected_head_revision(config)
    resolved = ScriptDirectory.from_config(config).get_revision(revision)
    if resolved is None:
        raise RuntimeError(f"Unknown Alembic revision: {revision}")
    return resolved.revision


def inspect_schema(*, database_url: str | None = None) -> dict[str, Any]:
    config = get_alembic_config(database_url)
    expected_head_revision = get_expected_head_revision(config)
    current_revision = get_current_revision(config)
    return {
        "status": "ok",
        "operation": "inspect",
        "expected_head_revision": expected_head_revision,
        "current_revision": current_revision,
        "schema_matches_expected_head": current_revision == expected_head_revision,
    }


def run_migrations(revision: str = "head", *, database_url: str | None = None) -> dict[str, Any]:
    config = get_alembic_config(database_url)
    previous_revision = get_current_revision(config)
    expected_head_revision = get_expected_head_revision(config)
    requested_revision_resolved = resolve_revision(config, revision)
    command.upgrade(config, revision)
    current_revision = get_current_revision(config)
    result = {
        "status": "ok",
        "operation": "migrate",
        "requested_revision": revision,
        "requested_revision_resolved": requested_revision_resolved,
        "previous_revision": previous_revision,
        "current_revision": current_revision,
        "expected_head_revision": expected_head_revision,
        "schema_matches_requested_revision": current_revision == requested_revision_resolved,
        "schema_matches_expected_head": current_revision == expected_head_revision,
    }
    if not result["schema_matches_requested_revision"]:
        raise RuntimeError(f"Schema revision mismatch after migration: {result}")
    return result


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    request = event or {}
    action = request.get("action", "migrate")

    if action == "inspect":
        result = inspect_schema()
        logger.info("schema_inspection_completed", extra={"outcome": "processed"})
        return result
    if action != "migrate":
        raise RuntimeError(f"Unsupported migration action: {action}")

    revision = request.get("revision", "head")
    result = run_migrations(revision)
    logger.info("migrations_completed", extra={"outcome": "processed"})
    return result
