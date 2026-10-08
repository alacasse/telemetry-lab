from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from processing_worker import processor
from sqlalchemy.orm import sessionmaker

from packages.config import Settings
from packages.db.base import Base
from packages.db.session import get_engine
from packages.logging.logging import JsonFormatter, get_logger
from packages.schemas.queue import QueueEnvelope, QueuePayload
from packages.tracking.query import read_trace


def message() -> QueueEnvelope:
    now = datetime.now(UTC)
    return QueueEnvelope(
        message_id=str(uuid4()),
        message_type="telemetry.received",
        published_at=now,
        source="ingestion-service",
        correlation_id=str(uuid4()),
        idempotency_key=str(uuid4()),
        payload=QueuePayload(
            event_id=str(uuid4()),
            building_id="test",
            zone_id="zone",
            event_timestamp=now,
            received_at=now,
            temperature_c=23,
            humidity_pct=45,
            occupancy=8,
            co2_ppm=1400,
            hvac_mode="ventilation",
            airflow_pct=40,
            raw_payload={"must_not_be_exposed": "private"},
        ),
    )


def test_failure_observation_survives_rollback_and_retry_keeps_attempt_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 'test.db'}")
    engine = get_engine(settings)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, autoflush=False)
    envelope = message()

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("private error text")

    with monkeypatch.context() as patch:
        patch.setattr(processor, "create_decision", fail)
        assert processor.process_envelope(envelope, sessions, settings, "transport").retryable
    failed = read_trace(sessions, envelope.correlation_id)
    assert failed["results"] == []
    assert [o["kind"] for o in failed["observations"]] == ["started", "failed"]
    assert len({o["attempt_id"] for o in failed["observations"]}) == 1
    assert "private" not in json.dumps(failed, default=str)

    # Post-commit diagnostic logging failure cannot cause a replay.
    with monkeypatch.context() as patch:
        patch.setattr(processor.logger, "info", fail)
        recovered = processor.process_envelope(envelope, sessions, settings, "transport")
    assert recovered.acknowledged and not recovered.retryable
    proof = read_trace(sessions, envelope.correlation_id)
    assert [o["kind"] for o in proof["observations"]] == [
        "started",
        "failed",
        "started",
        "processed",
    ]
    assert len({o["attempt_id"] for o in proof["observations"]}) == 2
    assert proof["results"][0]["decisions"][0]["recommended_airflow_pct"] == 55
    assert "private" not in json.dumps(proof, default=str)
    engine.dispose()


def test_json_logger_preserves_per_event_context(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        get_logger("test-worker").info(
            "processed", extra={"event_id": "event-1", "correlation_id": "simulation-1"}
        )
    result = json.loads(JsonFormatter().format(caplog.records[-1]))
    assert result["service"] == "test-worker"
    assert result["event_id"] == "event-1"
    assert result["correlation_id"] == "simulation-1"


def test_duplicate_without_start_and_missing_transport_do_not_invent_deliveries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import delete

    from packages.db.models import ProcessingObservation

    settings = Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 'duplicate.db'}")
    engine = get_engine(settings)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, autoflush=False)
    envelope = message()
    assert processor.process_envelope(envelope, sessions, settings).acknowledged
    first = read_trace(sessions, envelope.correlation_id)
    second = envelope.model_copy(
        update={
            "message_id": str(uuid4()),
            "payload": envelope.payload.model_copy(update={"event_id": str(uuid4())}),
        }
    )

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("diagnostic logger failed after commit")

    with monkeypatch.context() as patch:
        patch.setattr(processor.logger, "info", fail)
        result = processor.process_envelope(second, sessions, settings)
    assert result.outcome == "duplicate" and result.acknowledged and not result.retryable
    with sessions.begin() as session:
        session.execute(
            delete(ProcessingObservation).where(ProcessingObservation.kind == "started")
        )
    proof = read_trace(sessions, envelope.correlation_id, second.payload.event_id)
    assert [o["kind"] for o in proof["observations"]] == ["duplicate"]
    assert proof["results"][0]["decisions"] == first["results"][0]["decisions"]
    assert proof["sends"][0]["attempts"][0]["delivery"] == "unknown"
    engine.dispose()


def test_two_measurements_keep_decisions_and_duplicate_membership_separate(tmp_path: Path) -> None:
    from datetime import timedelta

    settings = Settings(_env_file=None, DATABASE_URL=f"sqlite:///{tmp_path / 'scenario.db'}")
    engine = get_engine(settings)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, autoflush=False)
    normal = message()
    normal.payload.co2_ppm = 700
    # SQLite drops timezone information; same-zone ordering is tested on PostgreSQL.
    high = normal.model_copy(update={
        "message_id": str(uuid4()), "idempotency_key": str(uuid4()),
        "payload": normal.payload.model_copy(update={
            "event_id": str(uuid4()), "co2_ppm": 1400, "zone_id": "other-zone",
            "event_timestamp": normal.payload.event_timestamp + timedelta(milliseconds=1),
        }),
    })
    assert processor.process_envelope(normal, sessions, settings, "normal-transport").acknowledged
    assert processor.process_envelope(high, sessions, settings, "high-transport").acknowledged
    resend = high.model_copy(update={
        "message_id": str(uuid4()),
        "payload": high.payload.model_copy(update={"event_id": str(uuid4())}),
    })
    assert processor.process_envelope(resend, sessions, settings, "resend-transport").acknowledged
    proof = read_trace(sessions, normal.correlation_id)
    assert proof["results"][0]["decisions"] == []
    assert proof["results"][1]["decisions"][0]["recommended_airflow_pct"] == 55
    assert proof["measurements"] == [
        {"original_event_id": normal.payload.event_id, "send_event_ids": [normal.payload.event_id]},
        {"original_event_id": high.payload.event_id,
         "send_event_ids": [high.payload.event_id, resend.payload.event_id]},
    ]
    engine.dispose()
