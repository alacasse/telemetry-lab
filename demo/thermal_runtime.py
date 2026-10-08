"""Local-only composition of the thermal runtime and its transport adapters."""

from __future__ import annotations

import json
import os
import signal
import threading
from collections.abc import Callable
from typing import cast
from urllib.parse import urlparse

import httpx

from demo.thermal_local import multiplier
from demo.thermal_queue import ThermalQueue
from packages.config import get_settings
from packages.db.session import create_session_factory
from packages.thermal.runtime import (
    RoomClock,
    command_publication,
    reading_publication,
    receive_commands,
)
from packages.thermal.service import create_thermostat, recover_simulations


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


def run() -> None:
    settings = get_settings()
    if settings.is_staging or os.environ.get("TELEMETRY_LAB_LOCAL_DEMO") != "1":
        raise RuntimeError("Thermal runtime is local only")
    multiplier()
    url = ingestion_url()
    queue = ThermalQueue(settings)
    sessions = create_session_factory(settings)
    with sessions() as session, session.begin():
        recover_simulations(session)
    if os.environ.get("THERMAL_INITIALIZE_THERMOSTAT", "1") == "1":
        with sessions() as session, session.begin():
            create_thermostat(session, multiplier=multiplier())
    clock = RoomClock(sessions)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    def bounded_loop(action: Callable[[], None], interval: float) -> None:
        while not stop.is_set():
            try:
                action()
            except Exception as exc:
                print(f"thermal retry: {type(exc).__name__}", flush=True)
            stop.wait(interval)

    def receive() -> None:
        receive_commands(clock, queue)

    with httpx.Client(timeout=httpx.Timeout(2, connect=1), trust_env=False) as client:

        def send_reading(body: str, simulation_id: str) -> dict:
            return publish_reading(client, url, body, simulation_id)

        threads = [
            threading.Thread(target=bounded_loop, args=(action, interval), daemon=True)
            for action, interval in (
                (clock.tick, 0.05),
                (lambda: reading_publication(sessions, send_reading), 0.1),
                (lambda: command_publication(sessions, queue), 0.1),
                (receive, 0.05),
            )
        ]
        for thread in threads:
            thread.start()
        print(
            json.dumps(
                {
                    "runtime": "local-thermal",
                    "process_id": os.getpid(),
                    "release_revision": settings.release_revision,
                }
            ),
            flush=True,
        )
        while not stop.wait(0.2):
            if not all(thread.is_alive() for thread in threads):
                raise RuntimeError("Thermal runtime thread failed")
        for thread in threads:
            thread.join(timeout=4)
    with sessions() as session, session.begin():
        recover_simulations(session)


if __name__ == "__main__":
    run()
