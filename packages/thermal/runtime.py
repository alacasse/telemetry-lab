"""Thermal clock and bounded publication workers with injected transports."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from packages.thermal.authority import Authority, AuthorityStopping
from packages.thermal.commands import CommandMessage, CommandRejected, parse_command_message
from packages.thermal.models import ThermalCommand, ThermalReading, ThermalSimulation
from packages.thermal.service import (
    advance_state,
    apply_command,
    expire_simulation,
    mark_published,
    observe_reading,
    recover_simulations,
    verify_command,
)


class CommandTransport(Protocol):
    """Command delivery operations; receive preserves Body and ReceiptHandle keys."""

    def publish(self, body: str) -> str: ...

    def receive(self) -> list[dict]: ...

    def acknowledge(self, receipt: str) -> None: ...


class RoomClock:
    """Serialize physical integration and actuator changes with monotonic anchors."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        authority: Authority,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sessions = sessions
        self.authority = authority
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
            except AuthorityStopping:
                raise
            except Exception:
                self.authority.fail()
                self.recovery_required = True
                self.anchors.clear()
                self.samples.clear()
                raise

    def _tick_transaction(self) -> None:
        with self.authority.transaction(self.sessions) as session:
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
            self.authority.heartbeat(session)
            for sid in set(anchors) - live:
                anchors.pop(sid, None)
                samples.pop(sid, None)

        self.anchors, self.samples, self.generations = anchors, samples, generations
        self.recovery_required = False

    def command(self, sid: str, sequence: int, command_type: str | None = None) -> bool:
        with self.lock:
            try:
                return self._command_transaction(CommandMessage(sid, sequence, command_type))
            except (CommandRejected, AuthorityStopping):
                raise
            except Exception:
                self.authority.fail()
                self.recovery_required = True
                self.anchors.clear()
                self.samples.clear()
                raise

    def _command_transaction(self, message: CommandMessage) -> bool:
        sid, sequence = message.simulation_id, message.sequence
        with self.authority.transaction(self.sessions) as session:
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


def _reading_publication(
    sessions: sessionmaker[Session],
    publish_reading: Callable[[str, str], dict],
    authority: Authority,
) -> None:
    # Oldest first prevents a slow transport from skipping causal measurements.
    with authority.transaction(sessions) as session:
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
    authority.check_active()
    receipt = publish_reading(body, sid)
    with authority.transaction(sessions) as session:
        mark_published(session, sid, seq, receipt=receipt)


def _command_publication(
    sessions: sessionmaker[Session], queue: CommandTransport, authority: Authority
) -> None:
    with authority.transaction(sessions) as session:
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
    authority.check_active()
    message_id = queue.publish(
        json.dumps({"simulation_id": sid, "sequence": seq, "command_type": command_type})
    )
    with authority.transaction(sessions) as session:
        command = session.get(ThermalCommand, (sid, seq))
        if command is not None and command.published_at is None:
            command.published_at = datetime.now(UTC)
            command.transport_message_id = message_id


def receive_commands(clock: RoomClock, queue: CommandTransport) -> None:
    """Discard permanently invalid deliveries; retry transaction and transport failures."""
    clock.authority.check_active()
    try:
        with clock.authority.transaction(clock.sessions):
            pass
    except AuthorityStopping:
        raise
    except Exception:
        clock.authority.fail()
        clock.recovery_required = True
        clock.anchors.clear()
        clock.samples.clear()
        raise
    clock.authority.check_active()
    for delivery in queue.receive():
        clock.authority.check_active()
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
            clock.authority.check_active()
            queue.acknowledge(delivery["ReceiptHandle"])


def reading_publication(
    sessions: sessionmaker[Session],
    publish_reading: Callable[[str, str], dict],
    authority: Authority,
) -> None:
    try:
        _reading_publication(sessions, publish_reading, authority)
    except SQLAlchemyError:
        authority.fail()
        raise


def command_publication(
    sessions: sessionmaker[Session], queue: CommandTransport, authority: Authority
) -> None:
    try:
        _command_publication(sessions, queue, authority)
    except SQLAlchemyError:
        authority.fail()
        raise
