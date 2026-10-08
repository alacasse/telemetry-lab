from __future__ import annotations

import json
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import quote

import boto3

from packages.config import Settings, get_settings

SECRET_CACHE_TTL_SECONDS = 300


@dataclass(slots=True)
class CachedSecret:
    value: str
    expires_at: float


_secret_cache: dict[tuple[str, str], CachedSecret] = {}


def clear_secret_cache() -> None:
    _secret_cache.clear()


def get_secrets_manager_client(settings: Settings | None = None) -> Any:
    resolved = settings or get_settings()
    client_kwargs: dict[str, Any] = {"region_name": resolved.aws_region}
    if resolved.aws_endpoint_url is not None:
        client_kwargs["endpoint_url"] = resolved.aws_endpoint_url
    return boto3.client("secretsmanager", **client_kwargs)


def get_secret_string(
    secret_id: str,
    *,
    settings: Settings | None = None,
    ttl_seconds: int = SECRET_CACHE_TTL_SECONDS,
) -> str:
    resolved = settings or get_settings()
    cache_key = (resolved.aws_region, secret_id)
    now = monotonic()
    cached = _secret_cache.get(cache_key)
    if cached is not None and cached.expires_at > now:
        return cached.value

    response = get_secrets_manager_client(resolved).get_secret_value(SecretId=secret_id)
    secret_value = response.get("SecretString")
    if not isinstance(secret_value, str) or not secret_value:
        raise RuntimeError(f"Secret {secret_id!r} did not return a SecretString payload")

    _secret_cache[cache_key] = CachedSecret(value=secret_value, expires_at=now + ttl_seconds)
    return secret_value


def get_secret_json(secret_id: str, *, settings: Settings | None = None) -> dict[str, Any]:
    payload = json.loads(get_secret_string(secret_id, settings=settings))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Secret {secret_id!r} must decode to a JSON object")
    return payload


def get_database_url_from_secret(secret_id: str, *, settings: Settings | None = None) -> str:
    payload = get_secret_json(secret_id, settings=settings)
    username = quote(str(payload["username"]))
    password = quote(str(payload["password"]))
    host = str(payload["host"])
    port = int(payload.get("port", 5432))
    database_name = payload.get("dbname") or payload.get("database") or payload.get("name")
    if not database_name:
        raise RuntimeError(f"Secret {secret_id!r} is missing a database name")

    engine = str(payload.get("engine", "postgresql+psycopg"))
    if engine == "postgres":
        engine = "postgresql+psycopg"
    return f"{engine}://{username}:{password}@{host}:{port}/{database_name}"
