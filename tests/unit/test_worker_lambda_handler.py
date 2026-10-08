from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from processing_worker import processor
from processing_worker.lambda_handler import process_event
from processing_worker.main import run_worker_iteration
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from packages.config import Settings
from packages.db.base import Base
from packages.db.models import BuildingLatestState, HvacDecision, TelemetryEvent, ZoneLatestState
from packages.db.session import get_engine
from packages.queue.memory import InMemoryQueueClient
from packages.schemas.queue import SCHEMA_VERSION, QueueEnvelope, QueuePayload


def build_message(*, timestamp: datetime, event_id: str | None = None) -> QueueEnvelope:
    resolved_event_id = event_id or str(uuid4())
    return QueueEnvelope(
        message_id=str(uuid4()),
        message_type="telemetry.received",
        schema_version=SCHEMA_VERSION,
        published_at=datetime.now(UTC),
        source="ingestion-service",
        correlation_id=str(uuid4()),
        idempotency_key=f"building-001:floor-02-east:{timestamp.isoformat()}",
        payload=QueuePayload(
            event_id=resolved_event_id,
            building_id="building-001",
            zone_id="floor-02-east",
            event_timestamp=timestamp,
            received_at=datetime.now(UTC),
            temperature_c=26.4,
            humidity_pct=43.1,
            occupancy=12,
            co2_ppm=780,
            hvac_mode="cooling",
            airflow_pct=65,
            raw_payload={"event_id": resolved_event_id},
        ),
    )


def build_sqs_event(records: list[tuple[str, str]]) -> dict[str, object]:
    return {
        "Records": [
            {
                "messageId": message_id,
                "receiptHandle": f"receipt-{message_id}",
                "body": body,
            }
            for message_id, body in records
        ]
    }


def create_session_factory(tmp_path: Path) -> tuple[Settings, sessionmaker]:
    db_path = tmp_path / "worker.sqlite"
    settings = Settings(_env_file=None, DATABASE_URL=f"sqlite:///{db_path}")
    engine = get_engine(settings)
    Base.metadata.create_all(engine)
    return settings, sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def test_worker_lambda_processes_nominal_sqs_batch(tmp_path: Path) -> None:
    settings, session_factory = create_session_factory(tmp_path)
    message = build_message(timestamp=datetime.now(UTC))

    response = process_event(
        build_sqs_event([("record-1", message.model_dump_json())]),
        settings=settings,
        session_factory=session_factory,
    )

    assert response == {"batchItemFailures": []}
    with session_factory() as session:
        assert session.scalar(select(TelemetryEvent.processing_status)) == "processed"


def test_failure_rolls_back_all_writes_and_redelivery_commits_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, session_factory = create_session_factory(tmp_path)
    message = build_message(timestamp=datetime.now(UTC))
    event = build_sqs_event([("record-retry", message.model_dump_json())])

    def fail_after_event_and_state_writes(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic persistence failure")

    with monkeypatch.context() as patch:
        patch.setattr(processor, "create_decision", fail_after_event_and_state_writes)
        response = process_event(event, settings=settings, session_factory=session_factory)

    assert response == {"batchItemFailures": [{"itemIdentifier": "record-retry"}]}
    with session_factory() as session:
        for model in (TelemetryEvent, ZoneLatestState, BuildingLatestState, HvacDecision):
            assert session.scalars(select(model)).all() == []

    for _ in range(2):
        assert process_event(event, settings=settings, session_factory=session_factory) == {
            "batchItemFailures": []
        }
    with session_factory() as session:
        for model in (TelemetryEvent, ZoneLatestState, BuildingLatestState, HvacDecision):
            assert len(session.scalars(select(model)).all()) == 1


def test_worker_lambda_returns_only_transient_failures(tmp_path: Path) -> None:
    settings, session_factory = create_session_factory(tmp_path)
    message = build_message(timestamp=datetime.now(UTC))

    response = process_event(
        build_sqs_event(
            [
                ("record-1", message.model_dump_json()),
                ("record-2", '{"broken": true}'),
            ]
        ),
        settings=settings,
        session_factory=session_factory,
    )

    assert response == {"batchItemFailures": [{"itemIdentifier": "record-2"}]}


def test_worker_lambda_acknowledges_duplicate_and_rejected_events(tmp_path: Path) -> None:
    settings, session_factory = create_session_factory(tmp_path)
    valid_timestamp = datetime.now(UTC)
    rejected_timestamp = datetime.now(UTC) + timedelta(minutes=30)
    original = build_message(timestamp=valid_timestamp, event_id="event-1")
    duplicate = build_message(timestamp=valid_timestamp, event_id="event-2")
    rejected = build_message(timestamp=rejected_timestamp, event_id="event-3")

    response = process_event(
        build_sqs_event(
            [
                ("record-1", original.model_dump_json()),
                ("record-2", duplicate.model_dump_json()),
                ("record-3", rejected.model_dump_json()),
            ]
        ),
        settings=settings,
        session_factory=session_factory,
    )

    assert response == {"batchItemFailures": []}
    with session_factory() as session:
        statuses = session.scalars(
            select(TelemetryEvent.processing_status).order_by(TelemetryEvent.event_id)
        ).all()
    assert statuses == ["processed", "rejected"]


def test_local_worker_iteration_still_processes_queue_messages(tmp_path: Path) -> None:
    settings, session_factory = create_session_factory(tmp_path)
    queue_client = InMemoryQueueClient()
    queue_client.publish_message(build_message(timestamp=datetime.now(UTC)))

    processed_count = run_worker_iteration(queue_client, session_factory, settings)

    assert processed_count == 1
    with session_factory() as session:
        assert session.scalar(select(TelemetryEvent.processing_status)) == "processed"
