from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from demo.thermal_runtime import publish_reading
from packages.db.base import Base
from packages.thermal.models import ThermalCommand, ThermalSimulation
from packages.thermal.runtime import RoomClock, reading_publication
from packages.thermal.service import create_simulation, recover_simulations, resume_simulation

Room = tuple[sessionmaker[Session], str, list[float], RoomClock]


def required[T](session: Session, model: type[T], identity: Any) -> T:
    row = session.get(model, identity)
    assert row is not None
    return row


@pytest.fixture
def room() -> Room:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    sid = str(uuid4())
    with sessions() as session, session.begin():
        create_simulation(session, sid)
        session.add(
            ThermalCommand(
                simulation_id=sid,
                sequence=1,
                reading_sequence=1,
                decision_id=str(uuid4()),
                heater_on=True,
                command_type="heating.start",
                created_at=datetime.now(UTC),
            )
        )
    now = [100.0]
    return sessions, sid, now, RoomClock(sessions, lambda: now[0])


def test_command_duplicate_does_not_lose_physical_elapsed(room: Room) -> None:
    sessions, sid, now, clock = room
    clock.tick()
    assert clock.command(sid, 1, "heating.start")
    now[0] += 1
    assert clock.command(sid, 1, "heating.start")
    now[0] += 1
    clock.tick()
    with sessions() as session:
        row = required(session, ThermalSimulation, sid)
        assert row.temperature_c == pytest.approx(19.6)
        assert row.simulated_seconds == pytest.approx(2)


def test_resume_between_ticks_does_not_catch_up(room: Room) -> None:
    sessions, sid, now, clock = room
    clock.tick()
    clock.command(sid, 1, "heating.start")
    with sessions() as session, session.begin():
        recover_simulations(session)
        deadline = required(session, ThermalSimulation, sid).expires_at
        resume_simulation(session, sid)
    now[0] += 10
    clock.tick()
    with sessions() as session:
        row = required(session, ThermalSimulation, sid)
        assert row.temperature_c == 19
        assert row.expires_at == deadline
    now[0] += 1
    clock.tick()
    with sessions() as session:
        assert required(session, ThermalSimulation, sid).temperature_c == pytest.approx(19.3)


def test_interrupted_command_is_deferred(room: Room) -> None:
    sessions, sid, now, clock = room
    with sessions() as session, session.begin():
        recover_simulations(session)
    assert not clock.command(sid, 1, "heating.start")
    with sessions() as session:
        assert required(session, ThermalCommand, (sid, 1)).status == "pending"
        assert not required(session, ThermalSimulation, sid).heater_on


def test_sensor_publishes_exact_durable_http_body(room: Room) -> None:
    sessions, sid, now, clock = room
    requests: list[httpx.Request] = []

    def accept(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202, json={"status": "accepted"})

    with httpx.Client(transport=httpx.MockTransport(accept)) as client:
        reading_publication(
            sessions,
            lambda body, sid: publish_reading(
                client, "http://localhost/ingestion/telemetry", body, sid
            ),
        )
    from packages.thermal.models import ThermalReading

    with sessions() as session:
        reading = required(session, ThermalReading, (sid, 1))
        assert requests[0].content.decode() == reading.body
        assert requests[0].headers["X-Correlation-Id"] == sid
        assert reading.published_at is not None
        assert reading.transport_receipt == {"status": "accepted"}


def test_reading_transport_receipt_and_identity_are_preserved(room: Room) -> None:
    from packages.thermal.models import ThermalReading

    sessions, sid, _, _ = room
    with sessions() as session:
        persisted_body = required(session, ThermalReading, (sid, 1)).body
    deliveries: list[tuple[str, str]] = []
    receipt = {"transport": "test-adapter", "message_id": "reading-accepted", "duplicate": False}

    def publish(body: str, simulation_id: str) -> dict:
        deliveries.append((body, simulation_id))
        return receipt

    reading_publication(sessions, publish)
    reading_publication(sessions, publish)
    assert deliveries == [(persisted_body, sid)]
    with sessions() as session:
        reading = required(session, ThermalReading, (sid, 1))
        assert reading.transport_receipt == receipt
        assert reading.publication_status == "published"


def test_sensor_cadence_does_not_follow_multiplier(room: Room) -> None:
    sessions, sid, now, clock = room
    with sessions() as session, session.begin():
        required(session, ThermalSimulation, sid).multiplier = 2
    clock.tick()
    clock.command(sid, 1, "heating.start")
    for _ in range(19):
        now[0] += 0.05
        clock.tick()
    from packages.thermal.models import ThermalReading

    with sessions() as session:
        row = required(session, ThermalSimulation, sid)
        assert row.temperature_c == pytest.approx(19.57)
        assert row.reading_sequence == 1
    now[0] += 0.051
    clock.tick()
    with sessions() as session:
        row = required(session, ThermalSimulation, sid)
        assert row.reading_sequence == 2
        assert required(session, ThermalReading, (sid, 2)).temperature_c == pytest.approx(19.6006)


def test_stop_integrates_time_under_old_actuator_state(room: Room) -> None:
    sessions, sid, now, clock = room
    clock.tick()
    clock.command(sid, 1, "heating.start")
    with sessions() as session, session.begin():
        row = required(session, ThermalSimulation, sid)
        row.controller_stopped = True
        session.add(
            ThermalCommand(
                simulation_id=sid,
                sequence=2,
                reading_sequence=1,
                decision_id=str(uuid4()),
                heater_on=False,
                command_type="heating.stop",
                created_at=datetime.now(UTC),
            )
        )
    now[0] += 2
    assert clock.command(sid, 2, "heating.stop")
    with sessions() as session:
        row = required(session, ThermalSimulation, sid)
        assert row.temperature_c == pytest.approx(19.6)
        assert not row.heater_on
    now[0] += 1
    clock.tick()
    with sessions() as session:
        assert required(session, ThermalSimulation, sid).temperature_c == pytest.approx(19.6)


def test_http_acceptance_before_receipt_commit_retries_exact_body(
    room: Room, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, sid, now, clock = room
    requests: list[bytes] = []

    def accept(request: httpx.Request) -> httpx.Response:
        requests.append(request.content)
        return httpx.Response(202, json={"status": "accepted"})

    import packages.thermal.runtime as runtime

    original = runtime.mark_published

    def unavailable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("receipt persistence interrupted")

    with httpx.Client(transport=httpx.MockTransport(accept)) as client:
        monkeypatch.setattr(runtime, "mark_published", unavailable)
        with pytest.raises(RuntimeError):
            reading_publication(
                sessions,
                lambda body, sid: publish_reading(
                    client, "http://localhost/ingestion/telemetry", body, sid
                ),
            )
        monkeypatch.setattr(runtime, "mark_published", original)
        reading_publication(
            sessions,
            lambda body, sid: publish_reading(
                client, "http://localhost/ingestion/telemetry", body, sid
            ),
        )
    assert len(requests) == 2
    assert requests[0] == requests[1]


def test_apply_commit_before_ack_redelivery_keeps_proof(room: Room) -> None:
    sessions, sid, now, clock = room
    clock.tick()
    assert clock.command(sid, 1, "heating.start")
    with sessions() as session:
        command = required(session, ThermalCommand, (sid, 1))
        original_proof = (command.applied_at, command.applied_state_version)
    assert clock.command(sid, 1, "heating.start")
    with sessions() as session:
        command = required(session, ThermalCommand, (sid, 1))
        assert (command.applied_at, command.applied_state_version) == original_proof


@pytest.mark.parametrize("speed", [0.5, 2])
def test_delayed_clock_samples_once_without_catchup_burst(room: Room, speed: float) -> None:
    sessions, sid, now, clock = room
    with sessions() as session, session.begin():
        required(session, ThermalSimulation, sid).multiplier = speed
    clock.tick()
    clock.command(sid, 1, "heating.start")
    now[0] += 5
    clock.tick()
    with sessions() as session:
        row = required(session, ThermalSimulation, sid)
        assert row.reading_sequence == 2
        assert row.temperature_c == pytest.approx(19 + 0.3 * speed * 5)
    clock.tick()
    with sessions() as session:
        assert required(session, ThermalSimulation, sid).reading_sequence == 2


def test_publication_failure_does_not_stop_clock_or_expiry(room: Room) -> None:
    from datetime import timedelta

    sessions, sid, now, clock = room
    clock.tick()
    clock.command(sid, 1, "heating.start")

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("transport unavailable")

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as client:
        with pytest.raises(httpx.ConnectError):
            reading_publication(
                sessions,
                lambda body, sid: publish_reading(
                    client, "http://localhost/ingestion/telemetry", body, sid
                ),
            )
    now[0] += 1
    clock.tick()
    with sessions() as session, session.begin():
        row = required(session, ThermalSimulation, sid)
        assert row.temperature_c == pytest.approx(19.3)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    clock.tick()
    with sessions() as session:
        row = required(session, ThermalSimulation, sid)
        assert row.status == "expired"
        assert not row.heater_on


def test_runtime_availability_is_separate_from_persisted_snapshot(
    room: Room, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    import os

    from demo.thermal_local import runtime_observation
    from packages.thermal.service import snapshot

    sessions, sid, now, clock = room
    state = tmp_path / "runtime.json"
    state.write_text(
        json.dumps(
            {
                "thermal_status": "unavailable",
                "thermal_pid": os.getpid(),
                "release_revision": "tested-revision",
            }
        )
    )
    monkeypatch.setenv("THERMAL_RUNTIME_STATE", str(state))
    with sessions() as session:
        result = snapshot(session, required(session, ThermalSimulation, sid))
        result["runtime"] = runtime_observation()
    assert result["runtime"]["status"] == "unavailable"
    assert result["runtime"]["release_revision"] == "tested-revision"
    assert result["status"] == "active"
    assert result["reading_sequence"] == 1
    with sessions() as session:
        assert required(session, ThermalSimulation, sid).status == "active"


def test_completion_between_discovery_and_lock_is_not_advanced_or_expired(
    room: Room, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    import packages.thermal.runtime as runtime

    sessions, sid, now, clock = room
    clock.tick()
    clock.command(sid, 1, "heating.start")
    with sessions() as session:
        row = required(session, ThermalSimulation, sid)
        previous = (row.temperature_c, row.simulated_seconds, row.step, row.reading_sequence)
    original_expire = runtime.expire_simulation
    changed = False

    def complete_before_lock(
        session: Session, simulation_id: str, now: datetime | None = None
    ) -> ThermalSimulation:
        nonlocal changed
        if not changed:
            changed = True
            with sessions() as concurrent, concurrent.begin():
                finished = required(concurrent, ThermalSimulation, sid)
                finished.status = "completed"
                finished.heater_on = False
                finished.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        return original_expire(session, simulation_id, now)

    monkeypatch.setattr(runtime, "expire_simulation", complete_before_lock)
    now[0] += 2
    clock.tick()
    with sessions() as session:
        row = required(session, ThermalSimulation, sid)
        assert row.status == "completed"
        assert not row.heater_on
        assert (
            row.temperature_c,
            row.simulated_seconds,
            row.step,
            row.reading_sequence,
        ) == previous


class DeliveryQueue:
    def __init__(self, bodies: list[str]) -> None:
        self.messages = [{"Body": body, "ReceiptHandle": str(i)} for i, body in enumerate(bodies)]
        self.acknowledged: list[str] = []

    def receive(self) -> list[dict[str, str]]:
        return self.messages

    def acknowledge(self, receipt: str) -> None:
        self.acknowledged.append(receipt)


def command_snapshot(room: Room) -> tuple:
    sessions, sid, _, clock = room
    with sessions() as session:
        simulation = required(session, ThermalSimulation, sid)
        command = required(session, ThermalCommand, (sid, 1))
        values = tuple(getattr(simulation, col.name) for col in simulation.__table__.columns)
        journal = tuple(getattr(command, col.name) for col in command.__table__.columns)
    return (
        values,
        journal,
        dict(clock.anchors),
        dict(clock.samples),
        dict(clock.generations),
        clock.recovery_required,
    )


@pytest.mark.parametrize(
    "case",
    [
        "malformed",
        "array",
        "null",
        "bool",
        "string",
        "zero",
        "missing",
        "null_type",
        "mismatch",
        "cooling",
        "unknown",
        "unknown_simulation",
    ],
)
def test_permanent_rejection_preserves_all_state_and_continues(
    room: Room, case: str, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from packages.thermal.runtime import receive_commands

    _, sid, now, clock = room
    clock.tick()
    before = command_snapshot(room)
    now[0] += 2
    body: dict[str, object] = {"simulation_id": sid, "sequence": 1, "command_type": "heating.start"}
    if case == "bool":
        body["sequence"] = True
    if case == "string":
        body["sequence"] = "1"
    if case == "zero":
        body["sequence"] = 0
    if case == "missing":
        body.pop("command_type")
    if case == "null_type":
        body["command_type"] = None
    if case == "mismatch":
        body["command_type"] = "heating.stop"
    if case == "cooling":
        body["command_type"] = "cooling.start"
    if case == "unknown":
        body["sequence"] = 50
    if case == "unknown_simulation":
        body["simulation_id"] = str(uuid4())
    payload = {"malformed": "invalid SECRET\nlog injection", "array": "[]", "null": "null"}.get(
        case, json.dumps(body)
    )
    queue = DeliveryQueue([payload])
    receive_commands(clock, queue)  # type: ignore[arg-type]
    assert queue.acknowledged == ["0"]
    assert command_snapshot(room) == before
    reason = {
        "missing": "legacy delivery is not allowed for this command",
        "mismatch": "message type differs from authoritative command",
        "cooling": "message type differs from authoritative command",
        "unknown": "unknown command",
        "unknown_simulation": "unknown simulation",
    }.get(case, "invalid thermal command message")
    assert capsys.readouterr().out == f"thermal command rejected: {reason}\n"
    queue = DeliveryQueue(
        [
            payload,
            json.dumps({"simulation_id": sid, "sequence": 1, "command_type": "heating.start"}),
        ]
    )
    receive_commands(clock, queue)  # type: ignore[arg-type]
    assert queue.acknowledged == ["0", "1"]
    assert not clock.recovery_required


def test_legacy_delivery_only_for_migrated_commands(room: Room) -> None:
    from packages.thermal.commands import CommandRejected

    sessions, sid, _, clock = room
    with pytest.raises(CommandRejected):
        clock.command(sid, 1)
    with sessions() as session, session.begin():
        required(session, ThermalCommand, (sid, 1)).legacy_wire_allowed = True
    assert clock.command(sid, 1)


def test_reserved_cooling_row_rejected_before_expiration(room: Room) -> None:
    from datetime import timedelta

    from packages.thermal.commands import CommandRejected

    sessions, sid, _, clock = room
    clock.tick()
    with sessions() as session, session.begin():
        required(session, ThermalCommand, (sid, 1)).command_type = "cooling.start"
        required(session, ThermalSimulation, sid).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    before = command_snapshot(room)
    with pytest.raises(CommandRejected):
        clock.command(sid, 1, "cooling.start")
    assert command_snapshot(room) == before


def test_command_transaction_failure_does_not_ack_and_requires_recovery(
    room: Room, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    import packages.thermal.runtime as runtime

    _, sid, _, clock = room
    clock.tick()
    before = command_snapshot(room)[:2]

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(runtime, "apply_command", fail)
    queue = DeliveryQueue(
        [json.dumps({"simulation_id": sid, "sequence": 1, "command_type": "heating.start"})]
    )
    with pytest.raises(RuntimeError):
        runtime.receive_commands(clock, queue)  # type: ignore[arg-type]
    assert not queue.acknowledged
    assert clock.recovery_required
    assert not clock.anchors
    assert not clock.samples
    assert command_snapshot(room)[:2] == before


def test_publication_emits_persisted_type(room: Room) -> None:
    import json

    from packages.thermal.runtime import command_publication

    sessions, sid, _, _ = room
    bodies: list[str] = []

    class Publisher:
        def publish(self, body: str) -> str:
            bodies.append(body)
            return "message-id"

    command_publication(sessions, Publisher())  # type: ignore[arg-type]
    assert json.loads(bodies[0]) == {
        "simulation_id": sid,
        "sequence": 1,
        "command_type": "heating.start",
    }


def test_command_commit_failure_rolls_back_without_ack(room: Room) -> None:
    import json

    from sqlalchemy import event

    from packages.thermal.runtime import receive_commands

    sessions, sid, _, clock = room
    clock.tick()
    before = command_snapshot(room)[:2]
    queue = DeliveryQueue(
        [json.dumps({"simulation_id": sid, "sequence": 1, "command_type": "heating.start"})]
    )

    def fail_commit(session: Session) -> None:
        raise RuntimeError("commit failed")

    event.listen(sessions.class_, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="commit failed"):
            receive_commands(clock, queue)  # type: ignore[arg-type]
    finally:
        event.remove(sessions.class_, "before_commit", fail_commit)
    assert not queue.acknowledged
    assert clock.recovery_required
    assert command_snapshot(room)[:2] == before


def test_rejection_logging_does_not_expose_unrecognized_exception_text(
    room: Room, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from packages.thermal.commands import CommandRejected
    from packages.thermal.runtime import receive_commands

    _, sid, _, clock = room

    def unsafe_reason(*args: object, **kwargs: object) -> bool:
        raise CommandRejected("SECRET\nforged log")

    monkeypatch.setattr(clock, "command", unsafe_reason)
    queue = DeliveryQueue(
        [json.dumps({"simulation_id": sid, "sequence": 1, "command_type": "heating.start"})]
    )
    receive_commands(clock, queue)  # type: ignore[arg-type]
    assert queue.acknowledged == ["0"]
    assert capsys.readouterr().out == "thermal command rejected: invalid command\n"


def test_cancelled_late_start_has_no_effect_or_anchor_mutation(room: Room) -> None:
    from packages.thermal.service import request_stop

    sessions, sid, now, clock = room
    clock.tick()
    with sessions() as session, session.begin():
        required(session, ThermalSimulation, sid).command_sequence = 1
        request_stop(session, sid)
        # Fixture's manual command has not updated the simulation sequence.
        # It still represents the persisted pending start cancelled by request.
    before_anchors = dict(clock.anchors)
    with sessions() as session:
        before = required(session, ThermalSimulation, sid).state_version
    now[0] += 5
    assert clock.command(sid, 1, "heating.start")
    assert clock.anchors == before_anchors
    with sessions() as session:
        row = required(session, ThermalSimulation, sid)
        assert row.state_version == before and not row.heater_on and row.temperature_c == 19


def test_command_publication_receipt_commit_failure_retries_same_identity(room: Room) -> None:
    import json

    from sqlalchemy import event

    from packages.thermal.runtime import command_publication

    sessions, sid, _, _ = room
    bodies: list[str] = []

    class Publisher:
        def publish(self, body: str) -> str:
            bodies.append(body)
            return f"accepted-{len(bodies)}"

    def fail_receipt_commit(session: Session) -> None:
        if bodies:
            raise RuntimeError("command receipt commit failed")

    event.listen(sessions.class_, "before_commit", fail_receipt_commit)
    try:
        with pytest.raises(RuntimeError, match="command receipt commit failed"):
            command_publication(sessions, Publisher())  # type: ignore[arg-type]
    finally:
        event.remove(sessions.class_, "before_commit", fail_receipt_commit)
    with sessions() as session:
        command = required(session, ThermalCommand, (sid, 1))
        assert command.published_at is None
        assert command.transport_message_id is None
        assert command.status == "pending"
    command_publication(sessions, Publisher())  # type: ignore[arg-type]
    assert len(bodies) == 2 and bodies[0] == bodies[1]
    assert json.loads(bodies[0]) == {
        "simulation_id": sid,
        "sequence": 1,
        "command_type": "heating.start",
    }
    with sessions() as session:
        command = required(session, ThermalCommand, (sid, 1))
        assert command.published_at is not None
        assert command.transport_message_id == "accepted-2"


def test_ack_failure_after_commit_redelivery_does_not_repeat_effect(room: Room) -> None:
    import json

    from packages.thermal.runtime import receive_commands

    sessions, sid, now, clock = room
    clock.tick()

    class LostAcknowledgementQueue(DeliveryQueue):
        def acknowledge(self, receipt: str) -> None:
            raise RuntimeError("acknowledgement unavailable")

    body = json.dumps({"simulation_id": sid, "sequence": 1, "command_type": "heating.start"})
    lost_ack = LostAcknowledgementQueue([body])
    with pytest.raises(RuntimeError, match="acknowledgement unavailable"):
        receive_commands(clock, lost_ack)  # type: ignore[arg-type]
    with sessions() as session:
        command = required(session, ThermalCommand, (sid, 1))
        assert command.status == "applied"
        proof = (command.applied_at, command.applied_state_version)
        assert required(session, ThermalSimulation, sid).heater_on
    committed_state = command_snapshot(room)
    now[0] += 2
    redelivery = DeliveryQueue([body])
    receive_commands(clock, redelivery)  # type: ignore[arg-type]
    assert redelivery.acknowledged == ["0"]
    assert command_snapshot(room) == committed_state
    assert not clock.recovery_required
    clock.tick()
    with sessions() as session:
        command = required(session, ThermalCommand, (sid, 1))
        assert (command.applied_at, command.applied_state_version) == proof
        assert required(session, ThermalSimulation, sid).temperature_c == pytest.approx(19.6)


def test_reading_receipt_commit_failure_retries_same_body_and_identity(room: Room) -> None:
    from sqlalchemy import event

    from packages.thermal.models import ThermalReading

    sessions, sid, _, _ = room
    with sessions() as session:
        original_body = required(session, ThermalReading, (sid, 1)).body
    deliveries: list[tuple[str, str]] = []

    def publish(body: str, simulation_id: str) -> dict:
        deliveries.append((body, simulation_id))
        return {"message_id": f"accepted-{len(deliveries)}", "duplicate": len(deliveries) > 1}

    def fail_receipt_commit(session: Session) -> None:
        if deliveries:
            raise RuntimeError("reading receipt commit failed")

    event.listen(sessions.class_, "before_commit", fail_receipt_commit)
    try:
        with pytest.raises(RuntimeError, match="reading receipt commit failed"):
            reading_publication(sessions, publish)
    finally:
        event.remove(sessions.class_, "before_commit", fail_receipt_commit)
    with sessions() as session:
        reading = required(session, ThermalReading, (sid, 1))
        assert reading.publication_status == "pending"
        assert reading.published_at is None
        assert reading.transport_receipt is None
    reading_publication(sessions, publish)
    assert deliveries == [(original_body, sid), (original_body, sid)]
    with sessions() as session:
        reading = required(session, ThermalReading, (sid, 1))
        assert reading.publication_status == "published"
        assert reading.published_at is not None
        assert reading.transport_receipt == {"message_id": "accepted-2", "duplicate": True}
