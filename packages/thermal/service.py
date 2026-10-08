from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from packages.db.models import HvacDecision
from packages.schemas.queue import QueuePayload
from packages.schemas.telemetry import HvacMode, TelemetryIn
from packages.thermal.commands import COMMAND_TYPES, CommandMessage, CommandRejected
from packages.thermal.models import (
    ThermalCommand,
    ThermalReading,
    ThermalSetting,
    ThermalSimulation,
)


class SimulationConflict(ValueError):  # noqa: N818
    pass


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def clock(now: datetime | None) -> datetime:
    return utc(now) if now is not None else datetime.now(UTC)


def locked(session: Session, simulation_id: str) -> ThermalSimulation:
    row = session.scalar(
        select(ThermalSimulation)
        .where(ThermalSimulation.simulation_id == simulation_id)
        .with_for_update()
    )
    if row is None:
        raise KeyError(simulation_id)
    return row


def expire_simulation(
    session: Session, simulation_id: str, now: datetime | None = None
) -> ThermalSimulation:
    row = locked(session, simulation_id)
    if (
        row.status in {"active", "interrupted", "stopping"}
        and row.expires_at is not None
        and clock(now) >= utc(row.expires_at)
    ):
        row.status = "expired"
        row.heater_on = False
        row.cooler_on = False
        row.state_version += 1
        for cmd in session.scalars(
            select(ThermalCommand).where(
                ThermalCommand.simulation_id == simulation_id, ThermalCommand.status == "pending"
            )
        ):
            cmd.status = "cancelled"
        for reading in session.scalars(
            select(ThermalReading).where(
                ThermalReading.simulation_id == simulation_id, ThermalReading.published_at.is_(None)
            )
        ):
            reading.publication_status = "cancelled"
    session.flush()
    return row


def create_simulation(
    session: Session,
    simulation_id: str,
    now: datetime | None = None,
    multiplier: float = 1.0,
    *,
    mode: str = "heating",
    predecessor_simulation_id: str | None = None,
) -> ThermalSimulation:
    simulation_id = str(UUID(simulation_id))
    if mode not in {"heating", "cooling"}:
        raise ValueError("unsupported mode")
    if predecessor_simulation_id is not None:
        predecessor_simulation_id = str(UUID(predecessor_simulation_id))
    if not math.isfinite(multiplier) or not 0.5 <= multiplier <= 2:
        raise ValueError("multiplier must be between 0.5 and 2")
    now = clock(now)
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(72410931)"))
    existing = session.scalar(
        select(ThermalSimulation)
        .where(ThermalSimulation.simulation_id == simulation_id)
        .with_for_update()
    )
    if existing is not None:
        if existing.mode != mode or existing.predecessor_simulation_id != predecessor_simulation_id:
            raise SimulationConflict("simulation identity differs")
        return expire_simulation(session, simulation_id, now)
    if predecessor_simulation_id is not None:
        try:
            predecessor = expire_simulation(session, predecessor_simulation_id, now)
        except KeyError as exc:
            raise SimulationConflict("predecessor stop is not confirmed") from exc
        if not stop_confirmed(session, predecessor):
            raise SimulationConflict("predecessor stop is not confirmed")
    for candidate in session.scalars(
        select(ThermalSimulation)
        .where(ThermalSimulation.status.in_(["active", "interrupted", "stopping"]))
        .with_for_update()
    ):
        expire_simulation(session, candidate.simulation_id, now)
        if candidate.status in {"active", "interrupted", "stopping"}:
            raise SimulationConflict("another simulation is still live")
    row = ThermalSimulation(
        simulation_id=simulation_id,
        building_id=f"thermal-{simulation_id}",
        created_at=now,
        expires_at=now + timedelta(seconds=30),
        multiplier=multiplier,
        mode=mode,
        temperature_c=19.0 if mode == "heating" else 25.0,
        predecessor_simulation_id=predecessor_simulation_id,
    )
    session.add(row)
    session.flush()
    observe(session, row, now)
    return row


def get_simulation(
    session: Session, simulation_id: str, now: datetime | None = None
) -> ThermalSimulation:
    return locked(session, simulation_id)


def resume_simulation(
    session: Session, simulation_id: str, now: datetime | None = None
) -> ThermalSimulation:
    row = expire_simulation(session, simulation_id, now)
    if row.status == "interrupted" and row.policy == "thermostat":
        row.status = "stopping" if row.stop_request_id else "active"
        row.stop_recovery_only = bool(row.stop_request_id)
        row.resume_generation += 1
        row.state_version += 1
        row.observation_requested = not bool(row.stop_request_id)
        if (
            row.phase == "stop_pending"
            and row.stop_command_sequence
            and _stop_evidence(session, row)
        ):
            command = session.get(ThermalCommand, (simulation_id, row.stop_command_sequence))
            if command is not None and command.final_reading_sequence is not None:
                final = session.get(ThermalReading, (simulation_id, command.final_reading_sequence))
                if final is not None:
                    process_thermostat(session, row, final, clock(now), True)
        session.flush()
        return row
    if row.status == "interrupted":
        final = session.scalar(
            select(ThermalReading).where(
                ThermalReading.simulation_id == simulation_id,
                ThermalReading.final.is_(True),
                ThermalReading.processed_at.is_not(None),
            )
        )
        if row.stop_request_id:
            row.status = "stopped" if _stop_evidence(session, row) else "stopping"
            row.stop_recovery_only = True
        else:
            row.stop_recovery_only = False
            row.status = "completed" if final is not None else "active"
        row.resume_generation += 1
        row.state_version += 1
    session.flush()
    return row


def recover_simulations(session: Session, now: datetime | None = None) -> list[ThermalSimulation]:
    rows = list(
        session.scalars(
            select(ThermalSimulation)
            .where(ThermalSimulation.status.in_(["active", "interrupted", "stopping"]))
            .with_for_update()
        )
    )
    for row in rows:
        expire_simulation(session, row.simulation_id, now)
        if row.status in {"active", "stopping"}:
            row.status = "interrupted"
            row.stop_recovery_only = True
            row.state_version += 1
    session.flush()
    return rows


def observe(
    session: Session, row: ThermalSimulation, now: datetime, final: bool = False
) -> ThermalReading:
    row.observation_requested = False
    row.reading_sequence += 1
    seq = row.reading_sequence
    telemetry = TelemetryIn(
        building_id=row.building_id,
        zone_id=row.zone_id,
        timestamp=now,
        temperature_c=row.temperature_c,
        humidity_pct=45,
        occupancy=8,
        co2_ppm=700,
        hvac_mode=(
            HvacMode.HEATING
            if row.heater_on
            else HvacMode.COOLING
            if row.cooler_on
            else HvacMode.OFF
        ),
        airflow_pct=40,
        simulation_id=row.simulation_id,
        reading_sequence=seq,
    )
    reading = ThermalReading(
        simulation_id=row.simulation_id,
        sequence=seq,
        step=row.step,
        state_version=row.state_version,
        simulated_seconds=row.simulated_seconds,
        observed_at=now,
        temperature_c=telemetry.temperature_c,
        heater_on=row.heater_on,
        cooler_on=row.cooler_on,
        final=final,
        settings_revision=row.settings_revision,
        cause="confirmation" if final else "setting" if row.policy == "thermostat" else "periodic",
        body=json.dumps(telemetry.model_dump(mode="json"), separators=(",", ":")),
    )
    session.add(reading)
    session.flush()
    return reading


def pending_readings(session: Session, simulation_id: str) -> list[ThermalReading]:
    return list(
        session.scalars(
            select(ThermalReading)
            .where(
                ThermalReading.simulation_id == simulation_id,
                ThermalReading.publication_status == "pending",
            )
            .order_by(ThermalReading.sequence)
        )
    )


def mark_published(
    session: Session,
    simulation_id: str,
    sequence: int,
    now: datetime | None = None,
    receipt: dict | None = None,
) -> None:
    locked(session, simulation_id)
    reading = session.get(ThermalReading, (simulation_id, sequence))
    if reading is None:
        raise KeyError(sequence)
    if reading.published_at is None:
        reading.published_at = clock(now)
        reading.publication_status = "published"
        reading.transport_receipt = receipt
    session.flush()


def process_reading(
    session: Session,
    payload: QueuePayload,
    now: datetime | None = None,
    controller_enabled: bool = True,
) -> ThermalCommand | None:
    now = clock(now)
    row = expire_simulation(session, payload.simulation_id or "", now)
    reading = session.get(ThermalReading, (row.simulation_id, payload.reading_sequence))
    if reading is None:
        raise ValueError("unknown server reading")
    validate_reading(session, payload)
    if reading.processed_at is None:
        reading.processed_at = now
        reading.processed_event_id = payload.event_id
    if row.policy == "thermostat":
        return process_thermostat(session, row, reading, now, controller_enabled)
    if (
        row.status in {"expired", "completed", "stopped"}
        or reading.sequence <= row.last_processed_reading
    ):
        return None
    if not controller_enabled:
        return None
    row.last_processed_reading = reading.sequence
    row.state_version += 1
    if reading.final and _stop_evidence(session, row):
        if row.status in {"active", "stopping"}:
            row.status = "stopped" if row.stop_request_id else "completed"
        return None
    if row.stop_request_id:
        return None
    desired = None
    if not row.controller_started:
        row.controller_started = True
        desired = True
    elif (
        reading.temperature_c >= 22 if row.mode == "heating" else reading.temperature_c <= 22
    ) and not row.controller_stopped:
        row.controller_stopped = True
        desired = False
    if desired is None:
        return None
    row.command_sequence += 1
    cmd = ThermalCommand(
        simulation_id=row.simulation_id,
        sequence=row.command_sequence,
        reading_sequence=reading.sequence,
        decision_id=str(uuid4()),
        heater_on=desired and row.mode == "heating",
        cooler_on=desired and row.mode == "cooling",
        command_type=f"{row.mode}.{'start' if desired else 'stop'}",
        legacy_wire_allowed=False,
        created_at=now,
    )
    session.add(cmd)
    if not desired:
        row.stop_command_sequence = cmd.sequence
    session.flush()
    return cmd


def advance_state(
    session: Session,
    simulation_id: str,
    step: int,
    elapsed_seconds: float,
    now: datetime | None = None,
) -> ThermalSimulation:
    row = expire_simulation(session, simulation_id, now)
    if row.status in {"active", "stopping"} and not row.stop_recovery_only and step > row.step:
        if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be finite and nonnegative")
        row.step = step
        row.simulated_seconds += elapsed_seconds * row.multiplier
        if row.heater_on:
            row.temperature_c += 0.3 * elapsed_seconds * row.multiplier
        elif row.cooler_on:
            row.temperature_c -= 0.3 * elapsed_seconds * row.multiplier
        row.state_version += 1
        session.flush()
    return row


def observe_reading(
    session: Session, simulation_id: str, now: datetime | None = None
) -> ThermalReading | None:
    row = expire_simulation(session, simulation_id, now)
    if row.policy == "thermostat":
        if row.status != "active" or row.stop_recovery_only:
            return None
        if row.observation_requested:
            return observe(session, row, clock(now))
        if not (row.heater_on or row.cooler_on):
            return None
        pending = session.scalar(
            select(ThermalReading).where(
                ThermalReading.simulation_id == simulation_id,
                ThermalReading.processed_at.is_(None),
                ThermalReading.cause == "periodic",
            )
        )
        if pending is not None:
            return None
        reading = observe(session, row, clock(now))
        reading.cause = "periodic"
        return reading
    if (
        row.status not in {"active", "stopping"}
        or row.stop_recovery_only
        or (row.controller_stopped and not row.heater_on and not row.cooler_on)
    ):
        return None
    return observe(session, row, clock(now))


def verify_command(session: Session, message: CommandMessage) -> ThermalCommand:
    """Verify under the simulation lock before expiration or physics can mutate state."""
    try:
        row = locked(session, message.simulation_id)
    except KeyError as exc:
        raise CommandRejected("unknown simulation") from exc
    command = session.scalar(
        select(ThermalCommand)
        .where(
            ThermalCommand.simulation_id == message.simulation_id,
            ThermalCommand.sequence == message.sequence,
        )
        .execution_options(populate_existing=True)
    )
    if command is None:
        raise CommandRejected("unknown command")
    if message.command_type is None:
        if not command.legacy_wire_allowed or command.command_type not in {
            "heating.start",
            "heating.stop",
        }:
            raise CommandRejected("legacy delivery is not allowed for this command")
    elif message.command_type != command.command_type:
        raise CommandRejected("message type differs from authoritative command")
    if command.command_type not in COMMAND_TYPES or (
        row.policy != "thermostat" and not command.command_type.startswith(f"{row.mode}.")
    ):
        raise CommandRejected("unsupported thermal actuator")
    if command.heater_on != (command.command_type == "heating.start") or command.cooler_on != (
        command.command_type == "cooling.start"
    ):
        raise CommandRejected("inconsistent heating projection")
    return command


def apply_command(
    session: Session,
    simulation_id: str,
    sequence: int,
    elapsed_seconds: float = 0,
    now: datetime | None = None,
    *,
    message: CommandMessage | None = None,
) -> ThermalCommand | None:
    if message is not None and (message.simulation_id, message.sequence) != (
        simulation_id,
        sequence,
    ):
        raise CommandRejected("message reference differs from requested command")
    if message is None:
        locked(session, simulation_id)
        stored = session.get(ThermalCommand, (simulation_id, sequence))
        if stored is None:
            return None
        message = CommandMessage(simulation_id, sequence, stored.command_type)
    command = verify_command(session, message)
    if command.status != "pending":
        return None
    now = clock(now)
    row = expire_simulation(session, simulation_id, now)
    if command.status != "pending" or row.status not in {"active", "stopping"}:
        return None
    if row.policy == "thermostat" and command.command_type.endswith(".start"):
        desired = thermostat_desired(row)
        if (
            command.settings_revision != row.settings_revision
            or desired != command.command_type.split(".")[0]
            or row.stop_request_id
            or row.phase == "stop_pending"
        ):
            command.status = "cancelled"
            row.observation_requested = True
            session.flush()
            return None
    earlier = session.scalar(
        select(ThermalCommand).where(
            ThermalCommand.simulation_id == simulation_id,
            ThermalCommand.sequence < sequence,
            ThermalCommand.status == "pending",
        )
    )
    if earlier is not None:
        return None
    advance_state(session, simulation_id, row.step + 1, elapsed_seconds, now)
    row.heater_on = command.command_type == "heating.start"
    row.cooler_on = command.command_type == "cooling.start"
    row.state_version += 1
    command.status = "applied"
    command.applied_at = clock(now)
    command.applied_state_version = row.state_version
    if row.policy == "thermostat":
        row.phase = "acting" if command.command_type.endswith(".start") else "stop_pending"
    if command.command_type.endswith(".stop"):
        final = observe(session, row, clock(now), final=True)
        command.final_reading_sequence = final.sequence
        row.stop_command_sequence = command.sequence
    session.flush()
    return command


def snapshot(session: Session, row: ThermalSimulation) -> dict:
    def values(
        item: ThermalSimulation | ThermalReading | ThermalCommand | ThermalSetting | HvacDecision,
    ) -> dict:
        return {
            column.name: getattr(item, column.name)
            for column in item.__table__.columns
            if column.name != "legacy_wire_allowed"
        }

    result = values(row)
    if row.policy == "thermostat":
        latest = session.scalar(
            select(ThermalReading)
            .where(
                ThermalReading.simulation_id == row.simulation_id,
                ThermalReading.processed_at.is_not(None),
            )
            .order_by(ThermalReading.sequence.desc())
            .limit(1)
        )
        command = session.get(ThermalCommand, (row.simulation_id, row.command_sequence))
        setting = session.scalar(
            select(ThermalSetting)
            .where(ThermalSetting.simulation_id == row.simulation_id)
            .order_by(ThermalSetting.revision.desc())
            .limit(1)
        )
        result.update(
            latest_reading=values(latest) if latest else None,
            latest_command=values(command) if command else None,
            latest_setting=values(setting) if setting else None,
            stop_confirmed=bool(row.stop_command_sequence and _stop_evidence(session, row)),
            readings=[],
            commands=[],
            decisions=[],
        )
        return result
    result["stop_confirmed"] = stop_confirmed(session, row)
    result["readings"] = [
        values(item)
        for item in session.scalars(
            select(ThermalReading)
            .where(ThermalReading.simulation_id == row.simulation_id)
            .order_by(ThermalReading.sequence)
        )
    ]
    for reading in result["readings"]:
        reading["correlation_id"] = row.simulation_id
    result["commands"] = [
        values(item)
        for item in session.scalars(
            select(ThermalCommand)
            .where(ThermalCommand.simulation_id == row.simulation_id)
            .order_by(ThermalCommand.sequence)
        )
    ]
    decision_ids = [command["decision_id"] for command in result["commands"]]
    result["decisions"] = [
        values(item)
        for item in session.scalars(
            select(HvacDecision).where(HvacDecision.decision_id.in_(decision_ids))
        )
    ]
    return result


def validate_reading(session: Session, payload: QueuePayload) -> ThermalReading:
    row = locked(session, payload.simulation_id or "")
    reading = session.get(ThermalReading, (row.simulation_id, payload.reading_sequence))
    if reading is None:
        raise ValueError("unknown server reading")
    expected = TelemetryIn.model_validate_json(reading.body)
    fields = (
        "building_id",
        "zone_id",
        "temperature_c",
        "humidity_pct",
        "occupancy",
        "co2_ppm",
        "hvac_mode",
        "airflow_pct",
    )
    if payload.event_timestamp != expected.timestamp or any(
        getattr(payload, field) != getattr(expected, field) for field in fields
    ):
        raise ValueError("reading does not match authoritative server observation")
    return reading


def _stop_evidence(session: Session, row: ThermalSimulation) -> bool:
    if row.heater_on or row.cooler_on:
        return False
    if row.stop_command_sequence is None:
        # Migrated heating evidence has no new links. Require the original
        # command application version to identify its processed final reading.
        if row.mode != "heating" or not row.controller_stopped:
            return False
        commands = list(
            session.scalars(
                select(ThermalCommand).where(
                    ThermalCommand.simulation_id == row.simulation_id,
                    ThermalCommand.command_type == "heating.stop",
                    ThermalCommand.status == "applied",
                )
            )
        )
        if (
            len(commands) != 1
            or commands[0].applied_at is None
            or commands[0].applied_state_version is None
        ):
            return False
        final = session.scalar(
            select(ThermalReading).where(
                ThermalReading.simulation_id == row.simulation_id,
                ThermalReading.final.is_(True),
                ThermalReading.state_version == commands[0].applied_state_version,
                ThermalReading.processed_at.is_not(None),
                ThermalReading.heater_on.is_(False),
                ThermalReading.cooler_on.is_(False),
            )
        )
        return final is not None
    command = session.get(ThermalCommand, (row.simulation_id, row.stop_command_sequence))
    if command is None or command.status != "applied" or command.applied_at is None:
        return False
    if (
        row.policy != "thermostat" and command.command_type != f"{row.mode}.stop"
    ) or not command.command_type.endswith(".stop"):
        return False
    if command.final_reading_sequence is None:
        if row.mode != "heating" or command.applied_state_version is None:
            return False
        final = session.scalar(
            select(ThermalReading).where(
                ThermalReading.simulation_id == row.simulation_id,
                ThermalReading.final.is_(True),
                ThermalReading.state_version == command.applied_state_version,
            )
        )
    else:
        final = session.get(ThermalReading, (row.simulation_id, command.final_reading_sequence))
    return bool(
        final is not None
        and final.final
        and final.processed_at is not None
        and not final.heater_on
        and not final.cooler_on
    )


def stop_confirmed(session: Session, row: ThermalSimulation) -> bool:
    return row.status in {"completed", "stopped"} and _stop_evidence(session, row)


def request_stop(
    session: Session, simulation_id: str, now: datetime | None = None
) -> ThermalSimulation:
    now = clock(now)
    row = expire_simulation(session, simulation_id, now)
    if row.status in {"completed", "stopped", "expired"}:
        return row
    if row.stop_request_id is None:
        row.stop_request_id = str(uuid4())
        row.stop_requested_at = now
    if row.status == "interrupted":
        row.stop_recovery_only = True
        row.resume_generation += 1
    row.status = "stopping"
    row.controller_stopped = True
    for command in session.scalars(
        select(ThermalCommand).where(
            ThermalCommand.simulation_id == simulation_id, ThermalCommand.status == "pending"
        )
    ):
        if command.command_type.endswith(".start"):
            command.status = "cancelled"
    if row.policy == "thermostat" and row.phase != "stop_pending":
        row.stop_command_sequence = None
    if row.policy == "thermostat":
        row.phase = "stop_pending"
    if row.stop_command_sequence is None:
        prior_stop = session.scalar(
            select(ThermalCommand)
            .where(
                ThermalCommand.simulation_id == simulation_id,
                ThermalCommand.command_type == f"{row.mode}.stop",
                ThermalCommand.status.in_(["pending", "applied"]),
            )
            .order_by(ThermalCommand.sequence.desc())
        )
        if prior_stop is not None and row.policy != "thermostat":
            row.stop_command_sequence = prior_stop.sequence
    if row.stop_command_sequence is None:
        row.command_sequence += 1
        applied_mode = (
            "heating" if row.heater_on else "cooling" if row.cooler_on else row.mode
        )
        command = ThermalCommand(
            simulation_id=simulation_id,
            sequence=row.command_sequence,
            command_type=f"{applied_mode}.stop",
            settings_revision=row.settings_revision,
            heater_on=False,
            cooler_on=False,
            origin="user",
            stop_request_id=row.stop_request_id,
            reading_sequence=None,
            decision_id=None,
            created_at=now,
        )
        session.add(command)
        row.stop_command_sequence = command.sequence
    if _stop_evidence(session, row):
        row.status = "stopped"
    session.flush()
    return row


def thermostat_desired(row: ThermalSimulation) -> str | None:
    if row.settled_revision == row.settings_revision:
        return None
    if row.mode == "heating" and row.temperature_c < row.target_c:
        return "heating"
    if row.mode == "cooling" and row.temperature_c > row.target_c:
        return "cooling"
    return None


def create_thermostat(
    session: Session, simulation_id: str | None = None, multiplier: float = 1
) -> ThermalSimulation:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(72410931)"))
    existing = session.scalar(
        select(ThermalSimulation)
        .where(ThermalSimulation.status.in_(["active", "interrupted", "stopping"]))
        .with_for_update()
    )
    if existing is not None:
        if existing.policy != "thermostat" or (
            simulation_id and existing.simulation_id != simulation_id
        ):
            raise SimulationConflict("another simulation is still live")
        return existing
    if simulation_id is None:
        previous = session.scalar(
            select(ThermalSimulation)
            .where(ThermalSimulation.policy == "thermostat")
            .order_by(ThermalSimulation.created_at.desc())
            .limit(1)
        )
        if previous is not None:
            return previous
    if simulation_id:
        existing = session.get(ThermalSimulation, simulation_id)
        if existing is not None:
            if existing.policy != "thermostat":
                raise SimulationConflict("simulation identity differs")
            return existing
    if not math.isfinite(multiplier) or not 0.5 <= multiplier <= 2:
        raise ValueError("multiplier must be between 0.5 and 2")
    sid = str(UUID(simulation_id)) if simulation_id else str(uuid4())
    row = ThermalSimulation(
        simulation_id=sid,
        building_id=f"thermal-{sid}",
        created_at=clock(None),
        expires_at=None,
        policy="thermostat",
        temperature_c=22,
        target_c=22,
        mode="heating",
        multiplier=multiplier,
        phase="idle",
    )
    session.add(row)
    session.flush()
    observe(session, row, clock(None))
    return row


def request_settings(
    session: Session,
    sid: str,
    operation_id: str,
    expected_revision: int,
    mode: str,
    target_c: float,
) -> ThermalSetting:
    row = locked(session, sid)
    operation_id = str(UUID(operation_id))
    existing = session.get(ThermalSetting, operation_id)
    if existing is not None:
        if (
            existing.simulation_id,
            existing.expected_revision,
            existing.mode,
            existing.target_c,
        ) != (sid, expected_revision, mode, target_c):
            raise SimulationConflict("operation identity differs")
        return existing
    if row.policy != "thermostat" or row.status != "active" or row.stop_request_id:
        raise SimulationConflict("thermostat is not accepting settings")
    if (
        mode not in {"heating", "cooling"}
        or not math.isfinite(target_c)
        or not 15 <= target_c <= 30
        or target_c * 2 != round(target_c * 2)
    ):
        raise ValueError("invalid thermostat setting")
    if expected_revision != row.settings_revision:
        raise SimulationConflict("settings revision differs")
    unchanged = (row.mode, row.target_c) == (mode, target_c)
    previously_settled = row.settled_revision == row.settings_revision
    row.settings_revision += 1
    setting = ThermalSetting(
        operation_id=operation_id,
        simulation_id=sid,
        expected_revision=expected_revision,
        revision=row.settings_revision,
        mode=mode,
        target_c=target_c,
        created_at=clock(None),
        origin="user",
    )
    session.add(setting)
    row.mode, row.target_c = mode, target_c
    # A settled request with identical values remains settled; a new target permits action.
    if unchanged and previously_settled:
        row.settled_revision = row.settings_revision
    for command in session.scalars(
        select(ThermalCommand).where(
            ThermalCommand.simulation_id == sid, ThermalCommand.status == "pending"
        )
    ):
        if command.command_type.endswith(".start"):
            if unchanged:
                command.settings_revision = row.settings_revision
            else:
                command.status = "cancelled"
    row.observation_requested = True
    row.state_version += 1
    session.flush()
    return setting


def thermostat_command(
    session: Session,
    row: ThermalSimulation,
    reading: ThermalReading,
    command_type: str,
    now: datetime,
) -> ThermalCommand:
    row.command_sequence += 1
    command = ThermalCommand(
        simulation_id=row.simulation_id,
        sequence=row.command_sequence,
        reading_sequence=reading.sequence,
        decision_id=str(uuid4()),
        heater_on=command_type == "heating.start",
        cooler_on=command_type == "cooling.start",
        command_type=command_type,
        created_at=now,
        settings_revision=row.settings_revision,
        origin="transition"
        if command_type.endswith(".stop") and thermostat_desired(row)
        else "automatic",
    )
    session.add(command)
    if command_type.endswith(".stop"):
        row.stop_command_sequence = command.sequence
        row.phase = "stop_pending"
    else:
        row.phase = "start_requested"
    session.flush()
    return command


def process_thermostat(
    session: Session, row: ThermalSimulation, reading: ThermalReading, now: datetime, enabled: bool
) -> ThermalCommand | None:
    if not enabled or row.status not in {"active", "stopping"}:
        return None
    # Confirmation is tied to the preserved command even if its revision is old.
    command = (
        session.get(ThermalCommand, (row.simulation_id, row.stop_command_sequence))
        if row.stop_command_sequence
        else None
    )
    if (
        row.phase == "stop_pending"
        and reading.final
        and command
        and command.final_reading_sequence == reading.sequence
        and _stop_evidence(session, row)
    ):
        row.phase = "idle"
        row.last_processed_reading = max(row.last_processed_reading, reading.sequence)
        if row.stop_request_id:
            row.status = "stopped"
            return None
        if command.settings_revision == row.settings_revision and command.origin != "transition":
            row.settled_revision = row.settings_revision
        row.observation_requested = (
            command.settings_revision != row.settings_revision or command.origin == "transition"
        )
        return None
    if (
        reading.settings_revision != row.settings_revision
        or reading.sequence <= row.last_processed_reading
        or row.stop_request_id
    ):
        return None
    row.last_processed_reading = reading.sequence
    row.state_version += 1
    if row.phase == "stop_pending":
        return None
    desired = thermostat_desired(row)
    applied = "heating" if row.heater_on else "cooling" if row.cooler_on else None
    if applied and applied != desired:
        return thermostat_command(session, row, reading, f"{applied}.stop", now)
    pending = session.scalar(
        select(ThermalCommand).where(
            ThermalCommand.simulation_id == row.simulation_id, ThermalCommand.status == "pending"
        )
    )
    if pending:
        return None
    if applied:
        row.phase = "acting"
    elif desired:
        return thermostat_command(session, row, reading, f"{desired}.start", now)
    else:
        row.phase = "idle"
        row.settled_revision = row.settings_revision
    return None


def history_page(session: Session, sid: str, kind: str, cursor: int = 0) -> dict:
    if kind == "decisions":
        entries = list(
            session.execute(
                select(ThermalCommand.sequence, HvacDecision)
                .join(HvacDecision, HvacDecision.decision_id == ThermalCommand.decision_id)
                .where(ThermalCommand.simulation_id == sid, ThermalCommand.sequence > cursor)
                .order_by(ThermalCommand.sequence)
                .limit(101)
            )
        )
        return {
            "items": [
                {c.name: getattr(item, c.name) for c in item.__table__.columns}
                for _, item in entries[:100]
            ],
            "next_cursor": entries[99][0] if len(entries) > 100 else None,
        }
    model: Any
    model = {"readings": ThermalReading, "commands": ThermalCommand, "settings": ThermalSetting}[
        kind
    ]
    sequence = ThermalSetting.revision if kind == "settings" else model.sequence
    items = list(
        session.scalars(
            select(model)
            .where(model.simulation_id == sid, sequence > cursor)
            .order_by(sequence)
            .limit(101)
        )
    )
    more = len(items) > 100
    items = items[:100]
    result: dict = {
        "items": [
            {
                c.name: getattr(item, c.name)
                for c in item.__table__.columns
                if c.name != "legacy_wire_allowed"
            }
            for item in items
        ],
        "next_cursor": getattr(items[-1], "revision" if kind == "settings" else "sequence")
        if more
        else None,
    }
    if kind == "readings":
        for item in result["items"]:
            item["correlation_id"] = sid
    return result
