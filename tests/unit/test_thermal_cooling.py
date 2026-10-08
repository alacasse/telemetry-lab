"""Behavioral coverage of symmetric control and durable stop admission."""

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.db.base import Base
from packages.schemas.queue import QueuePayload
from packages.thermal.models import ThermalCommand, ThermalReading
from packages.thermal.service import (
    SimulationConflict,
    advance_state,
    apply_command,
    create_simulation,
    expire_simulation,
    observe_reading,
    process_reading,
    recover_simulations,
    request_stop,
    snapshot,
)

NOW = datetime(2026, 10, 7, tzinfo=UTC)


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def reading_payload(reading: ThermalReading | None) -> QueuePayload:
    assert reading is not None
    body = json.loads(reading.body)
    body["event_timestamp"] = body.pop("timestamp")
    return QueuePayload(**body, event_id=str(uuid4()), received_at=NOW, raw_payload={})


def initial(session: Session, sid: str) -> ThermalReading:
    reading = session.get(ThermalReading, (sid, 1))
    assert reading is not None
    return reading


@pytest.mark.parametrize("mode,initial_c,direction", [("heating", 19, 1), ("cooling", 25, -1)])
@pytest.mark.parametrize("multiplier", [0.5, 1, 2])
def test_symmetric_overshoot_and_identified_confirmation(
    session: Session, mode: str, initial_c: float, direction: int, multiplier: float
) -> None:
    row = create_simulation(session, str(uuid4()), NOW, multiplier, mode=mode)
    assert row.temperature_c == initial_c and not row.heater_on and not row.cooler_on
    start = process_reading(session, reading_payload(initial(session, row.simulation_id)), NOW)
    assert start is not None and start.command_type == f"{mode}.start"
    apply_command(session, row.simulation_id, start.sequence, now=NOW)
    advance_state(session, row.simulation_id, row.step + 1, 11 / multiplier, NOW)
    assert row.temperature_c == pytest.approx(initial_c + direction * 3.3)
    stop = process_reading(
        session, reading_payload(observe_reading(session, row.simulation_id, NOW)), NOW
    )
    assert stop is not None and stop.command_type == f"{mode}.stop"
    apply_command(session, row.simulation_id, stop.sequence, now=NOW)
    assert not snapshot(session, row)["stop_confirmed"]
    final = session.get(ThermalReading, (row.simulation_id, stop.final_reading_sequence))
    process_reading(session, reading_payload(final), NOW)
    assert row.status == "completed" and snapshot(session, row)["stop_confirmed"]


@pytest.mark.parametrize("pending_start", [False, True])
def test_requested_stop_before_start_is_idempotent_and_admission_waits(
    session: Session, pending_start: bool
) -> None:
    row = create_simulation(session, str(uuid4()), NOW, mode="cooling")
    start = (
        process_reading(session, reading_payload(initial(session, row.simulation_id)), NOW)
        if pending_start
        else None
    )
    request_stop(session, row.simulation_id, NOW)
    command = session.get(ThermalCommand, (row.simulation_id, row.stop_command_sequence))
    assert command is not None and command.origin == "user"
    assert command.decision_id is None and command.reading_sequence is None
    before = snapshot(session, row)
    request_stop(session, row.simulation_id, NOW)
    assert snapshot(session, row) == before
    assert (
        process_reading(session, reading_payload(initial(session, row.simulation_id)), NOW) is None
    )
    if start is not None:
        assert start.status == "cancelled"
        assert apply_command(session, row.simulation_id, start.sequence, 10, NOW) is None
    with pytest.raises(SimulationConflict):
        create_simulation(session, str(uuid4()), NOW, predecessor_simulation_id=row.simulation_id)
    apply_command(session, row.simulation_id, command.sequence, now=NOW)
    assert row.temperature_c == 25
    final = session.get(ThermalReading, (row.simulation_id, command.final_reading_sequence))
    process_reading(session, reading_payload(final), NOW)
    assert row.status == "stopped" and snapshot(session, row)["stop_confirmed"]
    successor = create_simulation(
        session, str(uuid4()), NOW, predecessor_simulation_id=row.simulation_id
    )
    assert successor.mode == "heating"


def test_request_reuses_target_stop_and_recovery_preserves_origin(session: Session) -> None:
    row = create_simulation(session, str(uuid4()), NOW, mode="cooling")
    start = process_reading(session, reading_payload(initial(session, row.simulation_id)), NOW)
    assert start is not None
    apply_command(session, row.simulation_id, start.sequence, now=NOW)
    advance_state(session, row.simulation_id, row.step + 1, 11, NOW)
    stop = process_reading(
        session, reading_payload(observe_reading(session, row.simulation_id, NOW)), NOW
    )
    assert stop is not None
    request_stop(session, row.simulation_id, NOW)
    assert row.stop_command_sequence == stop.sequence and stop.origin == "automatic"
    recover_simulations(session, NOW)
    assert row.status == "interrupted"
    assert apply_command(session, row.simulation_id, stop.sequence, now=NOW) is None
    request_stop(session, row.simulation_id, NOW + timedelta(seconds=2))
    before = row.temperature_c
    apply_command(session, row.simulation_id, stop.sequence, 10, NOW + timedelta(seconds=2))
    assert row.temperature_c == before
    final = session.get(ThermalReading, (row.simulation_id, stop.final_reading_sequence))
    process_reading(session, reading_payload(final), NOW + timedelta(seconds=2))
    assert row.status == "stopped"


def test_expiry_before_final_confirmation_blocks_successor(session: Session) -> None:
    row = create_simulation(session, str(uuid4()), NOW, mode="cooling")
    request_stop(session, row.simulation_id, NOW)
    assert row.stop_command_sequence is not None
    apply_command(session, row.simulation_id, row.stop_command_sequence, now=NOW)
    command = session.get(ThermalCommand, (row.simulation_id, row.stop_command_sequence))
    assert command is not None
    expire_simulation(session, row.simulation_id, NOW + timedelta(seconds=30))
    final = session.get(ThermalReading, (row.simulation_id, command.final_reading_sequence))
    process_reading(session, reading_payload(final), NOW + timedelta(seconds=31))
    assert row.status == "expired" and not snapshot(session, row)["stop_confirmed"]
    with pytest.raises(SimulationConflict):
        create_simulation(
            session,
            str(uuid4()),
            NOW + timedelta(seconds=31),
            predecessor_simulation_id=row.simulation_id,
        )
    create_simulation(session, str(uuid4()), NOW + timedelta(seconds=31))


def test_uuid_replay_binds_mode_and_predecessor(session: Session) -> None:
    row = create_simulation(session, str(uuid4()), NOW, mode="cooling")
    assert create_simulation(session, row.simulation_id, NOW, mode="cooling") is row
    with pytest.raises(SimulationConflict):
        create_simulation(session, row.simulation_id, NOW)


@pytest.mark.parametrize("legacy_wire_allowed", [False, True])
def test_migrated_completed_evidence_without_new_links_remains_admissible(
    session: Session, legacy_wire_allowed: bool
) -> None:
    row = create_simulation(session, str(uuid4()), NOW)
    request_stop(session, row.simulation_id, NOW)
    command = session.get(ThermalCommand, (row.simulation_id, row.stop_command_sequence))
    assert command is not None
    apply_command(session, row.simulation_id, command.sequence, now=NOW)
    final = session.get(ThermalReading, (row.simulation_id, command.final_reading_sequence))
    process_reading(session, reading_payload(final), NOW)
    # Project onto the actual evidence columns available before 0005.
    row.status = "completed"
    row.stop_request_id = None
    row.stop_requested_at = None
    row.stop_command_sequence = None
    command.origin = "automatic"
    command.stop_request_id = None
    command.final_reading_sequence = None
    command.legacy_wire_allowed = legacy_wire_allowed
    session.flush()
    assert snapshot(session, row)["stop_confirmed"]
    create_simulation(
        session, str(uuid4()), NOW, mode="cooling", predecessor_simulation_id=row.simulation_id
    )


def test_final_processed_while_interrupted_requires_explicit_stop_continuation(
    session: Session,
) -> None:
    row = create_simulation(session, str(uuid4()), NOW, mode="cooling")
    request_stop(session, row.simulation_id, NOW)
    assert row.stop_command_sequence is not None
    apply_command(session, row.simulation_id, row.stop_command_sequence, now=NOW)
    command = session.get(ThermalCommand, (row.simulation_id, row.stop_command_sequence))
    assert command is not None
    recover_simulations(session, NOW)
    final = session.get(ThermalReading, (row.simulation_id, command.final_reading_sequence))
    process_reading(session, reading_payload(final), NOW)
    assert row.status == "interrupted" and not snapshot(session, row)["stop_confirmed"]
    request_stop(session, row.simulation_id, NOW)
    assert row.status == "stopped" and snapshot(session, row)["stop_confirmed"]


@pytest.mark.parametrize("applied", [False, True])
def test_migrated_stop_identity_is_reused_without_changing_provenance(
    session: Session, applied: bool
) -> None:
    row = create_simulation(session, str(uuid4()), NOW)
    start = process_reading(session, reading_payload(initial(session, row.simulation_id)), NOW)
    assert start is not None
    apply_command(session, row.simulation_id, start.sequence, now=NOW)
    advance_state(session, row.simulation_id, row.step + 1, 11, NOW)
    stop = process_reading(
        session, reading_payload(observe_reading(session, row.simulation_id, NOW)), NOW
    )
    assert stop is not None
    if applied:
        apply_command(session, row.simulation_id, stop.sequence, now=NOW)
    final_sequence = stop.final_reading_sequence
    row.stop_command_sequence = None
    stop.final_reading_sequence = None
    recover_simulations(session, NOW)
    request_stop(session, row.simulation_id, NOW)
    assert row.stop_command_sequence == stop.sequence and row.command_sequence == 2
    assert stop.origin == "automatic" and stop.stop_request_id is None
    if not applied:
        apply_command(session, row.simulation_id, stop.sequence, now=NOW)
        final_sequence = stop.final_reading_sequence
    final = session.get(ThermalReading, (row.simulation_id, final_sequence))
    process_reading(session, reading_payload(final), NOW)
    assert row.status == "stopped" and snapshot(session, row)["stop_confirmed"]
