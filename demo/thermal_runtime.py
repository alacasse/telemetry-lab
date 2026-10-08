"""Local-only composition of the thermal runtime and its transport adapters."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from urllib.parse import urlparse

import httpx
from botocore.exceptions import BotoCoreError, ClientError

from demo.thermal_local import multiplier
from demo.thermal_queue import ThermalQueue
from packages.config import get_settings
from packages.thermal.authority import PgAuthority, create_authority_session_factory
from packages.thermal.process import ThermalTransports, run_process


def ingestion_url() -> str:
    value = os.environ.get("THERMAL_INGESTION_URL", "http://127.0.0.1:8088/ingestion/telemetry")
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path != "/ingestion/telemetry"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("THERMAL_INGESTION_URL must name the local ingestion route")
    return value


def publish_reading(client: httpx.Client, url: str, body: str, simulation_id: str) -> dict:
    """Publish the persisted body and return the ingestion service's actual receipt."""
    response = client.post(
        url,
        content=body,
        headers={"X-Correlation-Id": simulation_id, "Content-Type": "application/json"},
    )
    response.raise_for_status()
    return cast(dict, response.json())


def run() -> int:
    settings = get_settings()
    if settings.is_staging or os.environ.get("TELEMETRY_LAB_LOCAL_DEMO") != "1":
        raise RuntimeError("Thermal runtime is local only")
    time_multiplier = multiplier()
    url = ingestion_url()
    sessions = create_authority_session_factory(settings)
    authority = PgAuthority(
        create_authority_session_factory(settings), revision=settings.release_revision
    )

    @contextmanager
    def transports() -> Iterator[ThermalTransports]:
        queue = ThermalQueue(settings)
        with httpx.Client(timeout=httpx.Timeout(2, connect=1), trust_env=False) as client:
            yield ThermalTransports(
                queue, lambda body, sid: publish_reading(client, url, body, sid)
            )

    return run_process(
        sessions, authority, time_multiplier=time_multiplier,
        initialize=os.environ.get("THERMAL_INITIALIZE_THERMOSTAT", "1") == "1",
        transports=transports, retry_exceptions=(httpx.HTTPError, BotoCoreError, ClientError),
        runtime_name="local-thermal", revision=settings.release_revision,
    )


if __name__ == "__main__":
    raise SystemExit(run())
