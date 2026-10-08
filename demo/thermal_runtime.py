"""Local-only composition of the thermal runtime and its transport adapters."""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from collections.abc import Callable
from typing import cast
from urllib.parse import urlparse

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from demo.thermal_local import multiplier
from demo.thermal_queue import ThermalQueue
from packages.config import get_settings
from packages.thermal.authority import (
    AuthorityBusy,
    PgAuthority,
    create_authority_session_factory,
)
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


def initialize_runtime(
    sessions: sessionmaker[Session], authority: PgAuthority, *, initialize: bool
) -> None:
    """Acquire and repair atomically, before any worker or transport starts."""
    try:
        with sessions() as session, session.begin():
            authority.acquire(session)
            session.execute(text("SELECT pg_advisory_xact_lock(72410931)"))
            recover_simulations(session)
            if initialize:
                create_thermostat(session, multiplier=multiplier())
    except Exception:
        authority.fail()
        raise


def run() -> int:
    settings = get_settings()
    if settings.is_staging or os.environ.get("TELEMETRY_LAB_LOCAL_DEMO") != "1":
        raise RuntimeError("Thermal runtime is local only")
    multiplier()
    url = ingestion_url()
    sessions = create_authority_session_factory(settings)
    # Renewal cannot queue behind the pool used by business transactions.
    authority = PgAuthority(
        create_authority_session_factory(settings), revision=settings.release_revision
    )
    try:
        initialize_runtime(
            sessions, authority,
            initialize=os.environ.get("THERMAL_INITIALIZE_THERMOSTAT", "1") == "1",
        )
    except AuthorityBusy:
        print("Thermal runtime refused: shared-room authority is occupied", flush=True)
        return 75

    stop = threading.Event()
    fatal = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    def fail(exc: Exception) -> None:
        authority.fail()
        fatal.set()
        stop.set()
        print(f"thermal fatal: {type(exc).__name__}", flush=True)

    def bounded_loop(action: Callable[[], None], interval: float) -> None:
        while not stop.is_set():
            try:
                authority.check_active()
                action()
            except (httpx.HTTPError, BotoCoreError, ClientError) as exc:
                print(f"thermal transport retry: {type(exc).__name__}", flush=True)
            except Exception as exc:
                fail(exc)
                return
            stop.wait(interval)

    threads: list[threading.Thread] = []
    try:
        queue = ThermalQueue(settings)
        clock = RoomClock(sessions, authority=authority)
        with httpx.Client(timeout=httpx.Timeout(2, connect=1), trust_env=False) as client:

            def send_reading(body: str, simulation_id: str) -> dict:
                return publish_reading(client, url, body, simulation_id)

            threads = [
                threading.Thread(target=bounded_loop, args=(action, interval), daemon=True)
                for action, interval in (
                    (authority.renew, 2.0),
                    (clock.tick, 0.05),
                    (lambda: reading_publication(sessions, send_reading, authority=authority), 0.1),
                    (lambda: command_publication(sessions, queue, authority=authority), 0.1),
                    (lambda: receive_commands(clock, queue), 0.05),
                )
            ]
            for thread in threads:
                thread.start()
            print(json.dumps({
                "runtime": "local-thermal", "process_id": os.getpid(),
                "release_revision": settings.release_revision,
            }), flush=True)
            while not stop.wait(0.2):
                authority.check_active()
                if not all(thread.is_alive() for thread in threads):
                    raise RuntimeError("Thermal runtime thread failed")
            deadline = time.monotonic() + 4
            for thread in threads:
                thread.join(timeout=max(0, deadline - time.monotonic()))
    except Exception as exc:
        fail(exc)
    if fatal.is_set() or any(thread.is_alive() for thread in threads):
        authority.fail()
        return 1
    try:
        authority.check_active()
        with sessions() as session, session.begin():
            authority.guard(session)
            recover_simulations(session)
            authority.release(session)
    except Exception as exc:
        fail(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
