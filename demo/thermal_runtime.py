"""Local room clock and bounded I/O workers. Recovery never resumes a simulation."""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from demo.thermal_queue import ThermalQueue
from demo.thermal_routes import multiplier
from packages.config import get_settings
from packages.db.session import create_session_factory
from packages.thermal.commands import CommandMessage, CommandRejected, parse_command_message
from packages.thermal.models import ThermalCommand, ThermalReading, ThermalSimulation
from packages.thermal.service import (
    advance_state,
    apply_command,
    create_thermostat,
    expire_simulation,
    mark_published,
    observe_reading,
    recover_simulations,
    verify_command,
)


class RoomClock:
    """Serialize physical integration and actuator changes with monotonic anchors."""

    def __init__(
        self, sessions: sessionmaker[Session], monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self.sessions = sessions
        self.monotonic = monotonic
        self.lock = threading.Lock()
        self.anchors: dict[str, float] = {}
        self.samples: dict[str, float] = {}
        self.generations: dict[str, int] = {}
        self.recovery_required = False

    def tick(self) -> None:
        with self.lock:
            try:
                self._tick_transaction()
            except Exception:
                self.recovery_required = True
                self.anchors.clear()
                self.samples.clear()
                raise

    def _tick_transaction(self) -> None:
        with self.sessions() as session, session.begin():
            if self.recovery_required:
                recover_simulations(session)
            anchors = dict(self.anchors)
            samples = dict(self.samples)
            generations = dict(self.generations)
            now = self.monotonic()
            simulation_ids = list(
                session.scalars(
                    select(ThermalSimulation.simulation_id).where(
                        ThermalSimulation.status.in_(["active", "interrupted", "stopping"])
                    )
                )
            )
            live = set()
            for sid in simulation_ids:
                row = expire_simulation(session, sid)
                if row.status not in {"active", "stopping"}:
                    anchors.pop(sid, None)
                    samples.pop(sid, None)
                    continue
                live.add(sid)
                if generations.get(sid) != row.resume_generation:
                    anchors.pop(sid, None)
                    samples.pop(sid, None)
                    generations[sid] = row.resume_generation
                anchor = anchors.get(sid, now)
                advance_state(session, sid, row.step + 1, max(0, now - anchor))
                anchors[sid] = now
                last_sample = samples.setdefault(sid, now)
                if row.observation_requested or now - last_sample >= 1:
                    observe_reading(session, sid)
                    samples[sid] = now
            for sid in set(anchors) - live:
                anchors.pop(sid, None)
                samples.pop(sid, None)

        self.anchors, self.samples, self.generations = anchors, samples, generations
        self.recovery_required = False

    def command(self, sid: str, sequence: int, command_type: str | None = None) -> bool:
        with self.lock:
            try:
                return self._command_transaction(CommandMessage(sid, sequence, command_type))
            except CommandRejected:
                raise
            except Exception:
                self.recovery_required = True
                self.anchors.clear()
                self.samples.clear()
                raise

    def _command_transaction(self, message: CommandMessage) -> bool:
        sid, sequence = message.simulation_id, message.sequence
        with self.sessions() as session, session.begin():
            command = verify_command(session, message)
            if command.status != "pending":
                return True
            row = expire_simulation(session, sid)
            now = self.monotonic()
            if self.recovery_required or row.status == "interrupted":
                return False  # Leave delivery unacknowledged until explicit resume.
            generation_changed = self.generations.get(sid) != row.resume_generation
            anchor = now if generation_changed else self.anchors.get(sid, now)
            if row.status in {"active", "stopping"}:
                advance_state(session, sid, row.step + 1, max(0, now - anchor))
            apply_command(session, sid, sequence, message=message)
            persisted = session.get(ThermalCommand, (sid, sequence))
            acknowledge = persisted is None or persisted.status != "pending"
            active = row.status in {"active", "stopping"}
            generation = row.resume_generation
        if active:
            self.anchors[sid] = now
            if generation_changed:
                self.samples.pop(sid, None)
            self.generations[sid] = generation
        return acknowledge  # Commit is complete before transport acknowledgement.


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


def reading_publication(sessions: sessionmaker[Session], client: httpx.Client, url: str) -> None:
    # Oldest first prevents a slow transport from skipping causal measurements.
    with sessions() as session, session.begin():
        reading = session.scalar(
            select(ThermalReading)
            .join(
                ThermalSimulation, ThermalSimulation.simulation_id == ThermalReading.simulation_id
            )
            .where(
                ThermalReading.publication_status == "pending",
                ThermalSimulation.status.in_(["active", "stopping"]),
            )
            .order_by(ThermalReading.observed_at)
            .limit(1)
        )
        if reading is None:
            return
        row = expire_simulation(session, reading.simulation_id)
        if row.status not in {"active", "stopping"}:
            return
        sid, seq, body = reading.simulation_id, reading.sequence, reading.body
    # No clock lock held across network I/O; final authority checked before initiating effect.
    response = client.post(
        url, content=body, headers={"X-Correlation-Id": sid, "Content-Type": "application/json"}
    )
    response.raise_for_status()
    with sessions() as session, session.begin():
        mark_published(session, sid, seq, receipt=response.json())


def command_publication(sessions: sessionmaker[Session], queue: ThermalQueue) -> None:
    with sessions() as session, session.begin():
        command = session.scalar(
            select(ThermalCommand)
            .join(
                ThermalSimulation, ThermalSimulation.simulation_id == ThermalCommand.simulation_id
            )
            .where(
                ThermalCommand.published_at.is_(None),
                ThermalCommand.status == "pending",
                ThermalSimulation.status.in_(["active", "stopping"]),
            )
            .order_by(ThermalCommand.created_at)
            .limit(1)
        )
        if command is None:
            return
        row = expire_simulation(session, command.simulation_id)
        if row.status not in {"active", "stopping"}:
            return
        sid, seq, command_type = command.simulation_id, command.sequence, command.command_type
    message_id = queue.publish(
        json.dumps({"simulation_id": sid, "sequence": seq, "command_type": command_type})
    )
    with sessions() as session, session.begin():
        command = session.get(ThermalCommand, (sid, seq))
        if command is not None and command.published_at is None:
            command.published_at = datetime.now(UTC)
            command.transport_message_id = message_id


def receive_commands(clock: RoomClock, queue: ThermalQueue) -> None:
    """Discard permanently invalid deliveries; retry transaction and transport failures."""
    for delivery in queue.receive():
        try:
            message = parse_command_message(delivery["Body"])
            acknowledge = clock.command(
                message.simulation_id, message.sequence, message.command_type
            )
        except CommandRejected as exc:
            # Only known developer-authored categories may cross the logging boundary.
            safe_reasons = {
                "invalid thermal command message",
                "unknown simulation",
                "unknown command",
                "legacy delivery is not allowed for this command",
                "message type differs from authoritative command",
                "unsupported thermal actuator",
                "inconsistent heating projection",
                "message reference differs from requested command",
            }
            reason = str(exc) if str(exc) in safe_reasons else "invalid command"
            print(f"thermal command rejected: {reason}", flush=True)
            acknowledge = True
        if acknowledge:
            queue.acknowledge(delivery["ReceiptHandle"])


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
        threads = [
            threading.Thread(target=bounded_loop, args=(action, interval), daemon=True)
            for action, interval in (
                (clock.tick, 0.05),
                (lambda: reading_publication(sessions, client, url), 0.1),
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
