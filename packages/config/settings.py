from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_ENV = "local"
STAGING_ENV = "staging"
SUPPORTED_APP_ENVS = {LOCAL_ENV, STAGING_ENV}
DEFAULT_DATABASE_URL = "postgresql+psycopg://telemetry_lab:telemetry_lab@localhost:5432/telemetry_lab"
STAGING_RUNTIME_ENV_FIELDS = {
    "APP_ENV": "app_env",
    "AWS_REGION": "aws_region",
    "DATABASE_SECRET_ARN": "database_secret_arn",
    "ENABLE_XRAY": "enable_xray",
    "LOG_LEVEL": "log_level",
    "QUEUE_BACKEND": "queue_backend",
    "QUEUE_NAME": "queue_name",
    "QUEUE_URL": "queue_url",
    "RELEASE_REVISION": "release_revision",
    "STAGING_AUTH_SECRET_ARN": "staging_auth_secret_arn",
}


class Settings(BaseSettings):
    app_env: str = Field(default=LOCAL_ENV, alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    database_url: str = Field(default=DEFAULT_DATABASE_URL, alias="DATABASE_URL")
    database_secret_arn: str | None = Field(default=None, alias="DATABASE_SECRET_ARN")
    queue_backend: str = Field(default="sqs", alias="QUEUE_BACKEND")
    queue_name: str = Field(default="telemetry-ingestion-queue", alias="QUEUE_NAME")
    queue_url: str | None = Field(default=None, alias="QUEUE_URL")
    aws_region: str = Field(default="ca-central-1", alias="AWS_REGION")
    aws_endpoint_url: str | None = Field(default=None, alias="AWS_ENDPOINT_URL")
    staging_auth_secret_arn: str | None = Field(default=None, alias="STAGING_AUTH_SECRET_ARN")
    enable_xray: bool = Field(default=False, alias="ENABLE_XRAY")
    release_revision: str = Field(default="dev", alias="RELEASE_REVISION")
    worker_receive_wait_seconds: int = Field(
        default=20,
        alias="WORKER_RECEIVE_WAIT_SECONDS",
        validation_alias=AliasChoices("WORKER_RECEIVE_WAIT_SECONDS", "WORKER_POLL_SECONDS"),
        ge=0,
        le=20,
    )
    worker_idle_sleep_seconds: float = Field(
        default=0.0,
        alias="WORKER_IDLE_SLEEP_SECONDS",
        ge=0.0,
    )
    worker_max_messages: int = Field(
        default=10,
        alias="WORKER_MAX_MESSAGES",
        ge=1,
        le=10,
    )
    ingestion_base_url: str = Field(default="http://localhost:8000", alias="INGESTION_BASE_URL")
    query_base_url: str = Field(default="http://localhost:8001", alias="QUERY_BASE_URL")
    simulator_batch_interval_seconds: float = Field(
        default=0.1,
        alias="SIMULATOR_BATCH_INTERVAL_SECONDS",
    )
    simulator_continuous_interval_seconds: float = Field(
        default=1.0,
        alias="SIMULATOR_CONTINUOUS_INTERVAL_SECONDS",
    )
    event_max_future_minutes: int = Field(default=15, alias="EVENT_MAX_FUTURE_MINUTES")
    event_max_past_days: int = Field(default=7, alias="EVENT_MAX_PAST_DAYS")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def __init__(self, **values: Any) -> None:
        if "_env_file" not in values:
            app_env = values.get("APP_ENV") or values.get("app_env") or os.getenv("APP_ENV")
            if app_env == STAGING_ENV:
                values["_env_file"] = None
        super().__init__(**values)

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, value: str) -> str:
        if value not in SUPPORTED_APP_ENVS:
            supported_values = ", ".join(sorted(SUPPORTED_APP_ENVS))
            raise ValueError(f"APP_ENV must be one of: {supported_values}")
        return value

    @model_validator(mode="after")
    def validate_staging_contract(self) -> Settings:
        if not self.is_staging:
            return self

        if "database_url" in self.model_fields_set:
            raise ValueError("DATABASE_URL must not be set when APP_ENV=staging")
        if self.queue_backend != "sqs":
            raise ValueError("QUEUE_BACKEND must be 'sqs' when APP_ENV=staging")
        return self

    @property
    def is_staging(self) -> bool:
        return self.app_env == STAGING_ENV

    @property
    def should_autocreate_queue(self) -> bool:
        return self.aws_endpoint_url is not None and not self.is_staging

    def require_staging_runtime_env_vars(self, *env_vars: str) -> None:
        if not self.is_staging:
            return

        missing: list[str] = []
        invalid: list[str] = []
        for env_var in env_vars:
            field_name = STAGING_RUNTIME_ENV_FIELDS.get(env_var)
            if field_name is None:
                raise ValueError(f"Unsupported staging runtime env var: {env_var}")
            if field_name not in self.model_fields_set:
                missing.append(env_var)
                continue

            value = getattr(self, field_name)
            if env_var in {"DATABASE_SECRET_ARN", "STAGING_AUTH_SECRET_ARN"} and not value:
                invalid.append(f"{env_var} must not be empty")
            if env_var == "QUEUE_BACKEND" and value != "sqs":
                invalid.append(f"{env_var} must be 'sqs'")
            if env_var == "APP_ENV" and value != STAGING_ENV:
                invalid.append(f"{env_var} must be '{STAGING_ENV}'")

        if not missing and not invalid:
            return

        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(sorted(missing))}")
        if invalid:
            details.extend(invalid)
        raise RuntimeError("staging runtime configuration error: " + "; ".join(details))

    def resolve_database_url(self) -> str:
        if not self.is_staging:
            return self.database_url

        self.require_staging_runtime_env_vars("APP_ENV", "DATABASE_SECRET_ARN", "QUEUE_BACKEND")
        from packages.aws.secrets import get_database_url_from_secret

        assert self.database_secret_arn is not None
        return get_database_url_from_secret(self.database_secret_arn, settings=self)

    def resolve_staging_auth_token(self) -> str | None:
        if not self.is_staging:
            return None

        self.require_staging_runtime_env_vars("APP_ENV", "STAGING_AUTH_SECRET_ARN")
        from packages.aws.secrets import get_secret_string

        assert self.staging_auth_secret_arn is not None
        return get_secret_string(self.staging_auth_secret_arn, settings=self)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
