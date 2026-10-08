"""Validate every standalone dependency before acquiring database authority."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from sqlalchemy.engine import make_url

from packages.config import Settings


def absolute_url(value: str, name: str, *, scheme: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError(f"{name} must be an absolute {scheme} URL") from None
    if (
        parsed.scheme != scheme or not parsed.hostname or parsed.username is not None
        or parsed.password is not None or parsed.query or parsed.fragment
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{name} must be an absolute {scheme} URL without credentials or query")
    host = parsed.hostname.lower()
    if port is not None and port != {"http": 80, "https": 443}[scheme]:
        host += f":{port}"
    return host, unquote(parsed.path).rstrip("/")


def required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value.strip():
        raise ValueError(f"{name} must be explicitly configured")
    return value


def validate_database_url(value: str) -> None:
    try:
        parsed = make_url(value)
        valid = parsed.get_backend_name() == "postgresql" and parsed.host and parsed.database
    except Exception:
        valid = False
    if not valid:
        raise ValueError("DATABASE_URL must name an absolute PostgreSQL database")


def queue_identity(value: str, name: str, *, mode: str, region: str) -> tuple[str, str]:
    host, path = absolute_url(value, name, scheme="https" if mode == "aws" else "http")
    parts = path.split("/")
    if mode == "aws":
        suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
        if host != f"sqs.{region}.{suffix}" or len(parts) != 3:
            raise ValueError(f"{name} must be a standard SQS URL for AWS_REGION")
        if not re.fullmatch(r"[0-9]{12}", parts[-2]):
            raise ValueError(f"{name} must include the SQS account identity")
    if len(parts) < 3 or not parts[-2] or not re.fullmatch(r"[\w-]+(?:\.fifo)?", parts[-1]):
        raise ValueError(f"{name} must include an account and queue name")
    if parts[-1].endswith(".fifo"):
        raise ValueError(f"{name} must name a standard SQS queue; FIFO is unsupported")
    # The same emulator queue may be addressed via localhost or Docker DNS.
    # AWS mode restricts the host to the configured region above.
    return parts[-2], parts[-1]


@dataclass(frozen=True)
class EngineConfig:
    settings: Settings
    transport_mode: str
    ingestion_url: str
    queue_url: str
    time_multiplier: float
    initialize: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EngineConfig:
        env = os.environ if env is None else env
        mode = required(env, "THERMAL_TRANSPORT_MODE")
        if mode not in {"local-emulator", "aws"}:
            raise ValueError("THERMAL_TRANSPORT_MODE must be local-emulator or aws")
        app_env = required(env, "APP_ENV")
        if app_env != ("staging" if mode == "aws" else "local"):
            raise ValueError("APP_ENV must match THERMAL_TRANSPORT_MODE")
        region = required(env, "AWS_REGION")
        revision = required(env, "RELEASE_REVISION")
        if env.get("QUEUE_BACKEND") != "sqs":
            raise ValueError("QUEUE_BACKEND must be explicitly configured as sqs")
        scheme = "https" if mode == "aws" else "http"
        ingestion = required(env, "THERMAL_INGESTION_URL")
        absolute_url(ingestion, "THERMAL_INGESTION_URL", scheme=scheme)
        queue = required(env, "THERMAL_QUEUE_URL")
        measurement_queue = required(env, "QUEUE_URL")
        command_identity = queue_identity(queue, "THERMAL_QUEUE_URL", mode=mode, region=region)
        measurement_identity = queue_identity(
            measurement_queue, "QUEUE_URL", mode=mode, region=region,
        )
        if command_identity == measurement_identity:
            raise ValueError("THERMAL_QUEUE_URL must differ from measurement QUEUE_URL")
        endpoint = env.get("AWS_ENDPOINT_URL")
        if mode == "aws":
            if any(key.startswith("AWS_ENDPOINT_URL") and value for key, value in env.items()):
                raise ValueError("AWS endpoint overrides are forbidden in aws mode")
            if "DATABASE_URL" in env:
                raise ValueError("DATABASE_URL must not be set when APP_ENV=staging")
            required(env, "DATABASE_SECRET_ARN")
            required(env, "STAGING_AUTH_SECRET_ARN")
        else:
            absolute_url(required(env, "AWS_ENDPOINT_URL"), "AWS_ENDPOINT_URL", scheme="http")
            if (
                env.get("AWS_ACCESS_KEY_ID") != "test"
                or env.get("AWS_SECRET_ACCESS_KEY") != "test"
                or env.get("AWS_SESSION_TOKEN")
            ):
                raise ValueError("local-emulator requires explicit test AWS credentials")
            validate_database_url(required(env, "DATABASE_URL"))
        try:
            multiplier = float(env.get("THERMAL_TIME_MULTIPLIER", "1"))
        except ValueError:
            raise ValueError("THERMAL_TIME_MULTIPLIER must be between 0.5 and 2") from None
        if not math.isfinite(multiplier) or not 0.5 <= multiplier <= 2:
            raise ValueError("THERMAL_TIME_MULTIPLIER must be between 0.5 and 2")
        initialize = env.get("THERMAL_INITIALIZE_THERMOSTAT", "1")
        if initialize not in {"0", "1"}:
            raise ValueError("THERMAL_INITIALIZE_THERMOSTAT must be 0 or 1")
        # Pass dependencies explicitly and disable dotenv for this composition.
        values: dict[str, object] = {
            "APP_ENV": app_env, "AWS_REGION": region, "RELEASE_REVISION": revision,
            "QUEUE_BACKEND": "sqs", "QUEUE_URL": measurement_queue,
            "AWS_ENDPOINT_URL": endpoint,
            "DATABASE_SECRET_ARN": env.get("DATABASE_SECRET_ARN"),
            "STAGING_AUTH_SECRET_ARN": env.get("STAGING_AUTH_SECRET_ARN"),
        }
        if mode == "local-emulator":
            values["DATABASE_URL"] = env["DATABASE_URL"]
        settings = Settings(_env_file=None, **values)
        return cls(settings, mode, ingestion, queue, multiplier, initialize == "1")
