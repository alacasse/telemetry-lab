from __future__ import annotations

from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch
from pydantic import ValidationError

from packages.config import DEFAULT_DATABASE_URL, Settings, get_settings


def test_worker_queue_settings_default_to_long_polling() -> None:
    settings = Settings(_env_file=None)

    assert settings.worker_receive_wait_seconds == 20
    assert settings.worker_idle_sleep_seconds == 0.0
    assert settings.worker_max_messages == 10


def test_worker_queue_settings_support_legacy_poll_alias() -> None:
    settings = Settings(_env_file=None, WORKER_POLL_SECONDS=5)

    assert settings.worker_receive_wait_seconds == 5


def test_staging_ignores_dotenv(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LOG_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "staging")

    settings = Settings()

    assert settings.log_level == "INFO"
    assert settings.database_url == DEFAULT_DATABASE_URL


def test_get_settings_ignores_dotenv_in_staging(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LOG_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "staging")
    get_settings.cache_clear()

    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert settings.log_level == "INFO"


def test_settings_disable_queue_autocreate_in_staging() -> None:
    settings = Settings(APP_ENV="staging", AWS_ENDPOINT_URL="http://localhost:4566")

    assert settings.should_autocreate_queue is False


def test_settings_reject_unsupported_environments() -> None:
    with pytest.raises(ValidationError, match="APP_ENV must be one of"):
        Settings(APP_ENV="prod")


def test_staging_rejects_clear_database_url() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL must not be set"):
        Settings(
            APP_ENV="staging",
            DATABASE_URL="postgresql+psycopg://telemetry_lab:secret@db.internal:5432/telemetry_lab",
        )


def test_staging_requires_explicit_database_secret_for_database_consumers() -> None:
    settings = Settings(APP_ENV="staging")

    with pytest.raises(RuntimeError, match="DATABASE_SECRET_ARN"):
        settings.require_staging_runtime_env_vars("DATABASE_SECRET_ARN")


def test_staging_accepts_secret_arns_for_runtime_contract() -> None:
    settings = Settings(
        APP_ENV="staging",
        DATABASE_SECRET_ARN="arn:aws:secretsmanager:ca-central-1:123456789012:secret:db",
        STAGING_AUTH_SECRET_ARN="arn:aws:secretsmanager:ca-central-1:123456789012:secret:auth",
        QUEUE_BACKEND="sqs",
        RELEASE_REVISION="abc123",
        ENABLE_XRAY=True,
    )

    settings.require_staging_runtime_env_vars(
        "APP_ENV",
        "DATABASE_SECRET_ARN",
        "STAGING_AUTH_SECRET_ARN",
        "QUEUE_BACKEND",
        "RELEASE_REVISION",
        "ENABLE_XRAY",
    )
