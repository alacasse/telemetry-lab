import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.db.base import Base
from packages.schemas.queue import QueueEnvelope, QueuePayload
from packages.thermal.models import ThermalReading
from packages.thermal.service import (
    SimulationConflict,
    advance_state,
    apply_command,
    create_simulation,
    expire_simulation,
    observe_reading,
    process_reading,
    recover_simulations,
    resume_simulation,
)


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


NOW = datetime(2026, 10, 7, tzinfo=UTC)


def reading_payload(reading: ThermalReading | None) -> QueuePayload:
    assert reading is not None
    body = json.loads(reading.body)
    body["event_timestamp"] = body.pop("timestamp")
    return QueuePayload(**body, event_id=str(uuid4()), received_at=NOW, raw_payload={})


def test_controller_cycle_and_idempotent_steps(session: Session) -> None:
    row = create_simulation(session, str(uuid4()), NOW)
    first = observe_reading(session, row.simulation_id, NOW)
    cmd = process_reading(session, reading_payload(first), NOW)
    assert cmd is not None
    assert cmd.heater_on
    apply_command(session, row.simulation_id, cmd.sequence, now=NOW)
    advance_state(session, row.simulation_id, 100, 9.999, NOW)
    before = row.temperature_c
    advance_state(session, row.simulation_id, 100, 9.999, NOW)
    assert row.temperature_c == before
    reading = observe_reading(session, row.simulation_id, NOW)
    assert process_reading(session, reading_payload(reading), NOW) is None
    advance_state(session, row.simulation_id, 101, 0.01, NOW)
    reading = observe_reading(session, row.simulation_id, NOW)
    stop = process_reading(session, reading_payload(reading), NOW)
    assert stop is not None
    assert not stop.heater_on
    apply_command(session, row.simulation_id, stop.sequence, now=NOW)
    assert row.status == "active"
    from packages.thermal.models import ThermalReading

    final = session.get(ThermalReading, (row.simulation_id, row.reading_sequence))
    process_reading(session, reading_payload(final), NOW)
    assert row.status == "completed"
    assert not row.heater_on
    assert row.command_sequence == 2


def test_expiry_and_interruption_do_not_restore_heating(session: Session) -> None:
    row = create_simulation(session, str(uuid4()), NOW)
    from packages.thermal.models import ThermalReading

    first = session.get(ThermalReading, (row.simulation_id, 1))
    recover_simulations(session, NOW)
    cmd = process_reading(session, reading_payload(first), NOW)
    assert cmd is not None
    assert row.status == "interrupted"
    assert apply_command(session, row.simulation_id, cmd.sequence, now=NOW) is None
    resume_simulation(session, row.simulation_id, NOW)
    apply_command(session, row.simulation_id, cmd.sequence, now=NOW)
    assert row.heater_on
    expire_simulation(session, row.simulation_id, NOW + timedelta(seconds=30))
    assert row.status == "expired" and not row.heater_on
    assert resume_simulation(session, row.simulation_id, NOW).status == "expired"


def test_admission_identity_and_forged_reading(session: Session) -> None:
    identity = str(uuid4())
    row = create_simulation(session, identity, NOW)
    assert create_simulation(session, identity, NOW) is row
    with pytest.raises(SimulationConflict):
        create_simulation(session, str(uuid4()), NOW)
    from packages.thermal.models import ThermalReading

    first = session.get(ThermalReading, (identity, 1))
    payload = reading_payload(first).model_copy(update={"temperature_c": 23})
    with pytest.raises(ValueError):
        process_reading(session, payload, NOW)
    assert row.last_processed_reading == 0


@pytest.mark.parametrize("multiplier", [0.5, 1, 2])
def test_rate_frozen_lifetime_and_off_state(session: Session, multiplier: float) -> None:
    row = create_simulation(session, str(uuid4()), NOW, multiplier)
    advance_state(session, row.simulation_id, 1, 2, NOW)
    assert row.temperature_c == 19
    from packages.thermal.models import ThermalReading

    first = session.get(ThermalReading, (row.simulation_id, 1))
    cmd = process_reading(session, reading_payload(first), NOW)
    assert cmd is not None
    apply_command(session, row.simulation_id, cmd.sequence, elapsed_seconds=2, now=NOW)
    assert row.temperature_c == 19
    advance_state(session, row.simulation_id, row.step + 1, 2, NOW)
    assert row.temperature_c == pytest.approx(19 + 0.6 * multiplier)
    deadline = row.expires_at
    recover_simulations(session, NOW + timedelta(seconds=5))
    resume_simulation(session, row.simulation_id, NOW + timedelta(seconds=20))
    assert row.expires_at == deadline
    assert row.temperature_c == pytest.approx(19 + 0.6 * multiplier)
    expire_simulation(session, row.simulation_id, NOW + timedelta(seconds=30))
    assert row.status == "expired"


def test_processed_order_uses_processed_not_generated_sequence(session: Session) -> None:
    row = create_simulation(session, str(uuid4()), NOW)
    second = observe_reading(session, row.simulation_id, NOW)
    third = observe_reading(session, row.simulation_id, NOW)
    assert row.reading_sequence == 3
    first_cmd = process_reading(session, reading_payload(second), NOW)
    assert first_cmd is not None and row.last_processed_reading == 2
    assert process_reading(session, reading_payload(second), NOW) is None
    assert process_reading(session, reading_payload(third), NOW) is None
    assert row.last_processed_reading == 3
    from packages.thermal.models import ThermalReading

    first = session.get(ThermalReading, (row.simulation_id, 1))
    assert process_reading(session, reading_payload(first), NOW) is None
    assert row.last_processed_reading == 3 and row.command_sequence == 1


def test_stop_delivery_before_start_does_not_apply_out_of_order(session: Session) -> None:
    row = create_simulation(session, str(uuid4()), NOW)
    from packages.thermal.models import ThermalReading

    first = session.get(ThermalReading, (row.simulation_id, 1))
    start = process_reading(session, reading_payload(first), NOW)
    # A server observation may already be hot; receipt of the stop still cannot
    # overtake the pending start command in the command delivery stream.
    row.temperature_c = 22
    hot = observe_reading(session, row.simulation_id, NOW)
    stop = process_reading(session, reading_payload(hot), NOW)
    assert start is not None and stop is not None
    assert apply_command(session, row.simulation_id, stop.sequence, now=NOW) is None
    assert not row.heater_on
    apply_command(session, row.simulation_id, start.sequence, now=NOW)
    apply_command(session, row.simulation_id, stop.sequence, now=NOW)
    assert not row.heater_on
    assert start is not None and stop is not None
    assert apply_command(session, row.simulation_id, stop.sequence, now=NOW) is None


def test_expired_command_delivery_cannot_reenable_output(session: Session) -> None:
    row = create_simulation(session, str(uuid4()), NOW)
    first = session.get(ThermalReading, (row.simulation_id, 1))
    command = process_reading(session, reading_payload(first), NOW)
    assert command is not None
    assert (
        apply_command(
            session,
            row.simulation_id,
            command.sequence,
            elapsed_seconds=10,
            now=NOW + timedelta(seconds=30),
        )
        is None
    )
    assert row.status == "expired" and not row.heater_on
    assert command.status == "cancelled"


def test_worker_duplicate_never_creates_orphan_command(session: Session) -> None:
    from processing_worker.processor import process_queue_message
    from sqlalchemy import func, select
    from sqlalchemy.orm import sessionmaker

    from packages.config import Settings
    from packages.db.models import TelemetryEvent
    from packages.thermal.models import ThermalCommand

    row = create_simulation(session, str(uuid4()))
    first = session.get(ThermalReading, (row.simulation_id, 1))
    payload = reading_payload(first)
    factory = sessionmaker(bind=session.bind)
    session.commit()
    envelope = QueueEnvelope(
        message_id=str(uuid4()),
        message_type="telemetry.received",
        published_at=NOW,
        source="ingestion-service",
        correlation_id=row.simulation_id,
        idempotency_key="shared-key",
        payload=payload.model_copy(update={"simulation_id": None, "reading_sequence": None}),
    )
    assert process_queue_message(envelope, factory, Settings()).outcome == "processed"
    replay = envelope.model_copy(
        update={"payload": payload.model_copy(update={"event_id": str(uuid4())})}
    )
    assert process_queue_message(replay, factory, Settings()).outcome == "duplicate"
    session.expire_all()
    reading = session.get(ThermalReading, (row.simulation_id, 1))
    assert reading is not None and reading.processed_event_id == payload.event_id
    assert session.scalar(select(func.count()).select_from(ThermalCommand)) == 0
    assert session.scalar(select(func.count()).select_from(TelemetryEvent)) == 1


def test_worker_rollback_keeps_command_decision_and_event_atomic(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import processing_worker.processor as processor
    from sqlalchemy import func, select
    from sqlalchemy.orm import sessionmaker

    from packages.config import Settings
    from packages.db.models import HvacDecision, TelemetryEvent
    from packages.thermal.models import ThermalCommand

    row = create_simulation(session, str(uuid4()))
    first = session.get(ThermalReading, (row.simulation_id, 1))
    payload = reading_payload(first)
    factory = sessionmaker(bind=session.bind)
    session.commit()
    envelope = QueueEnvelope(
        message_id=str(uuid4()),
        message_type="telemetry.received",
        published_at=NOW,
        source="ingestion-service",
        correlation_id=row.simulation_id,
        idempotency_key="atomic-key",
        payload=payload,
    )

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("decision persistence fails")

    monkeypatch.setattr(processor, "create_decision", fail)
    with pytest.raises(RuntimeError):
        processor.process_queue_message(envelope, factory, Settings())
    session.expire_all()
    for model in (TelemetryEvent, HvacDecision, ThermalCommand):
        assert session.scalar(select(func.count()).select_from(model)) == 0
    assert not row.controller_started and row.last_processed_reading == 0


def test_rejected_command_never_expires_or_advances_state(session: Session) -> None:
    from packages.thermal.commands import CommandMessage, CommandRejected
    from packages.thermal.service import snapshot

    row = create_simulation(session, str(uuid4()), NOW)
    command = process_reading(
        session, reading_payload(session.get(ThermalReading, (row.simulation_id, 1))), NOW
    )
    assert command is not None
    assert command.command_type == "heating.start" and not command.legacy_wire_allowed
    session.refresh(command)
    before = snapshot(session, row)
    for message_type in (None, "heating.stop", "cooling.start"):
        with pytest.raises(CommandRejected):
            apply_command(
                session,
                row.simulation_id,
                command.sequence,
                elapsed_seconds=20,
                now=NOW + timedelta(seconds=31),
                message=CommandMessage(row.simulation_id, command.sequence, message_type),
            )
        assert snapshot(session, row) == before
    assert "legacy_wire_allowed" not in before["commands"][0]


def test_direct_application_rejects_reserved_actuator_before_mutation(session: Session) -> None:
    from packages.thermal.commands import CommandRejected
    from packages.thermal.service import snapshot

    row = create_simulation(session, str(uuid4()), NOW)
    command = process_reading(
        session, reading_payload(session.get(ThermalReading, (row.simulation_id, 1))), NOW
    )
    assert command is not None
    command.command_type = "cooling.start"
    session.flush()
    session.refresh(command)
    before = snapshot(session, row)
    with pytest.raises(CommandRejected):
        apply_command(session, row.simulation_id, command.sequence, 20, NOW + timedelta(seconds=31))
    assert snapshot(session, row) == before


def test_direct_application_rejects_inconsistent_projection(session: Session) -> None:
    from packages.thermal.commands import CommandRejected

    row = create_simulation(session, str(uuid4()), NOW)
    command = process_reading(
        session, reading_payload(session.get(ThermalReading, (row.simulation_id, 1))), NOW
    )
    assert command is not None
    from sqlalchemy import text

    session.execute(text("PRAGMA ignore_check_constraints = ON"))
    session.execute(
        text("UPDATE thermal_commands SET heater_on = false WHERE simulation_id = :id"),
        {"id": row.simulation_id},
    )
    with pytest.raises(CommandRejected):
        apply_command(session, row.simulation_id, command.sequence, 20, NOW + timedelta(seconds=31))
    assert row.status == "active" and row.step == 0 and row.temperature_c == 19
