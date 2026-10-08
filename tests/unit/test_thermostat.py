import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.db.base import Base
from packages.schemas.queue import QueuePayload
from packages.thermal.models import (
    ThermalCommand,
    ThermalReading,
    ThermalSetting,
    ThermalSimulation,
)
from packages.thermal.service import (
    SimulationConflict,
    advance_state,
    apply_command,
    create_thermostat,
    expire_simulation,
    history_page,
    observe_reading,
    process_reading,
    recover_simulations,
    request_settings,
    request_stop,
    resume_simulation,
    snapshot,
)

NOW = datetime.now(UTC)


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def process(session: Session, reading: ThermalReading | None) -> ThermalCommand | None:
    assert reading is not None
    body = json.loads(reading.body)
    body["event_timestamp"] = body.pop("timestamp")
    return process_reading(
        session, QueuePayload(**body, event_id=str(uuid4()), received_at=NOW, raw_payload={})
    )


def setting(
    session: Session, row: ThermalSimulation, mode: str, target: float, operation: str | None = None
) -> ThermalSetting:
    return request_settings(
        session, row.simulation_id, operation or str(uuid4()), row.settings_revision, mode, target
    )


def sample(session: Session, row: ThermalSimulation) -> ThermalReading | None:
    return observe_reading(session, row.simulation_id)


def test_initial_idle_and_no_deadline(session: Session) -> None:
    row = create_thermostat(session)
    assert row.temperature_c == row.target_c == 22
    assert row.expires_at is None
    assert snapshot(session, row)["latest_reading"] is None
    assert process(session, session.get(ThermalReading, (row.simulation_id, 1))) is None
    assert row.phase == "idle" and row.command_sequence == 0
    assert sample(session, row) is None
    assert expire_simulation(session, row.simulation_id, NOW + timedelta(days=1)).status == "active"


def test_idempotence_conflicts_and_noop_pending_start(session: Session) -> None:
    row = create_thermostat(session)
    op = str(uuid4())
    first = setting(session, row, "heating", 24, op)
    assert request_settings(session, row.simulation_id, op, 0, "heating", 24) is first
    with pytest.raises(SimulationConflict):
        request_settings(session, row.simulation_id, op, 0, "cooling", 24)
    with pytest.raises(SimulationConflict):
        request_settings(session, row.simulation_id, str(uuid4()), 0, "heating", 24)
    cmd = process(session, sample(session, row))
    setting(session, row, "heating", 24)
    assert process(session, sample(session, row)) is None
    assert row.command_sequence == 1
    assert cmd is not None
    apply_command(session, row.simulation_id, cmd.sequence)
    assert row.heater_on


def test_inversion_waits_exact_stop_then_opposite_start(session: Session) -> None:
    row = create_thermostat(session)
    setting(session, row, "heating", 24)
    start = process(session, sample(session, row))
    assert start is not None
    apply_command(session, row.simulation_id, start.sequence)
    setting(session, row, "cooling", 20)
    stop = process(session, sample(session, row))
    assert stop is not None
    assert stop.command_type == "heating.stop"
    assert row.phase == "stop_pending"
    assert stop is not None
    apply_command(session, row.simulation_id, stop.sequence)
    final = session.get(ThermalReading, (row.simulation_id, stop.final_reading_sequence))
    assert row.phase == "stop_pending" and sample(session, row) is None
    assert process(session, final) is None
    opposite = process(session, sample(session, row))
    assert opposite is not None
    assert opposite.command_type == "cooling.start"
    apply_command(session, row.simulation_id, opposite.sequence)
    assert start is not None
    apply_command(session, row.simulation_id, start.sequence)
    process(session, final)
    assert row.cooler_on and not row.heater_on and row.phase == "acting"


def test_overshoot_no_restart_and_terminal_distinction(session: Session) -> None:
    row = create_thermostat(session)
    setting(session, row, "heating", 22.5)
    start = process(session, sample(session, row))
    assert start is not None
    apply_command(session, row.simulation_id, start.sequence)
    advance_state(session, row.simulation_id, 10, 2)
    stop = process(session, sample(session, row))
    advance_state(session, row.simulation_id, 11, 1)
    assert stop is not None
    apply_command(session, row.simulation_id, stop.sequence)
    process(session, session.get(ThermalReading, (row.simulation_id, stop.final_reading_sequence)))
    assert row.temperature_c == pytest.approx(22.9)
    assert row.status == "active" and row.phase == "idle"
    setting(session, row, "heating", 22.5)
    assert process(session, sample(session, row)) is None
    request_stop(session, row.simulation_id)
    assert row.status == "stopping"
    apply_command(session, row.simulation_id, row.command_sequence)
    process(session, session.get(ThermalReading, (row.simulation_id, row.reading_sequence)))
    assert row.status == "stopped"
    assert create_thermostat(session) is row


def test_stale_reading_cancelled_start_and_recovery(session: Session) -> None:
    row = create_thermostat(session)
    setting(session, row, "heating", 24)
    old = sample(session, row)
    start = process(session, old)
    setting(session, row, "cooling", 20)
    assert process(session, old) is None
    assert start is not None
    apply_command(session, row.simulation_id, start.sequence)
    assert not row.heater_on
    newer = process(session, sample(session, row))
    assert newer is not None
    apply_command(session, row.simulation_id, newer.sequence)
    temp = row.temperature_c
    recover_simulations(session)
    assert create_thermostat(session) is row
    assert row.status == "interrupted"
    advance_state(session, row.simulation_id, 100, 100)
    assert row.temperature_c == temp
    resume_simulation(session, row.simulation_id)
    assert row.observation_requested


def test_periodic_pending_bounded_and_history(session: Session) -> None:
    row = create_thermostat(session)
    setting(session, row, "heating", 30)
    start = process(session, sample(session, row))
    assert start is not None
    apply_command(session, row.simulation_id, start.sequence)
    reading = sample(session, row)
    assert reading is not None
    assert reading.cause == "periodic"
    assert sample(session, row) is None
    setting(session, row, "heating", 29)
    requested = sample(session, row)
    assert requested is not None and requested.cause == "setting"
    for _ in range(105):
        setting(session, row, "heating", 29)
    page = history_page(session, row.simulation_id, "settings")
    assert len(page["items"]) == 100 and page["next_cursor"] == 100
    assert len(history_page(session, row.simulation_id, "settings", 100)["items"]) == 7


@pytest.mark.parametrize("kind", ["target", "inversion", "terminal"])
def test_resume_reconciles_only_exact_processed_stop(session: Session, kind: str) -> None:
    row = create_thermostat(session)
    setting(session, row, "heating", 22.5)
    start = process(session, sample(session, row))
    assert start is not None
    apply_command(session, row.simulation_id, start.sequence)
    if kind == "terminal":
        setting(session, row, "cooling", 20)
        request_stop(session, row.simulation_id)
        stop_sequence = row.command_sequence
        terminal = session.get(ThermalCommand, (row.simulation_id, stop_sequence))
        assert terminal is not None and terminal.settings_revision == row.settings_revision
        assert terminal.command_type == "heating.stop"
    else:
        if kind == "target":
            advance_state(session, row.simulation_id, 10, 2)
        else:
            setting(session, row, "cooling", 20)
        stop = process(session, sample(session, row))
        assert stop is not None
        stop_sequence = stop.sequence
    apply_command(session, row.simulation_id, stop_sequence)
    final = session.get(ThermalReading, (row.simulation_id, row.reading_sequence))
    recover_simulations(session)
    process(session, final)
    assert row.phase == "stop_pending"
    resume_simulation(session, row.simulation_id)
    assert row.phase == "idle"
    if kind == "terminal":
        assert row.status == "stopped"
    elif kind == "inversion":
        opposite = process(session, sample(session, row))
        assert opposite is not None and opposite.command_type == "cooling.start"
    else:
        assert row.status == "active" and row.settled_revision == row.settings_revision
        assert sample(session, row) is None


def test_identical_values_while_acting_do_not_transfer_ancient_settlement(session: Session) -> None:
    row = create_thermostat(session)
    process(session, session.get(ThermalReading, (row.simulation_id, 1)))
    assert row.settled_revision == 0
    setting(session, row, "heating", 30)
    start = process(session, sample(session, row))
    assert start is not None
    apply_command(session, row.simulation_id, start.sequence)
    setting(session, row, "heating", 30)
    assert row.settings_revision == 2 and row.settled_revision == 0
    assert process(session, sample(session, row)) is None
    assert row.command_sequence == 1 and row.phase == "acting" and row.heater_on
