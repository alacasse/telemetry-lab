from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from ingestion_service import main as ingestion
from processing_worker import processor
from processing_worker.lambda_handler import process_event
from query_service import main as query
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from packages.config import Settings
from packages.db.migration_handler import run_migrations
from packages.db.models import BuildingLatestState, HvacDecision, TelemetryEvent, ZoneLatestState
from packages.db.session import get_engine
from packages.queue.memory import InMemoryQueueClient


def test_postgres_migration_rollback_retry_duplicate_and_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = os.getenv("TELEMETRY_LAB_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Run scripts/test-postgres.sh for an isolated PostgreSQL database")
    settings = Settings(_env_file=None, APP_ENV="local", DATABASE_URL=url, QUEUE_BACKEND="inmemory")
    assert run_migrations(database_url=url)["schema_matches_expected_head"]
    engine = get_engine(settings)
    sessions = sessionmaker(bind=engine, autoflush=False)
    queue = InMemoryQueueClient()
    building = f"test-{uuid4()}"
    payload = {
        "building_id": building,
        "zone_id": "synthetic-zone",
        "timestamp": datetime.now(UTC).isoformat(),
        "temperature_c": 26.7,
        "humidity_pct": 44.0,
        "occupancy": 12,
        "co2_ppm": 780,
        "hvac_mode": "cooling",
        "airflow_pct": 65,
    }
    monkeypatch.setattr(
        ingestion.app,
        "dependency_overrides",
        {
            ingestion.get_settings_dependency: lambda: settings,
            ingestion.get_queue_client_dependency: lambda: queue,
        },
    )
    monkeypatch.setattr(
        query.app,
        "dependency_overrides",
        {
            query.get_settings_dependency: lambda: settings,
            query.get_session_factory_dependency: lambda: sessions,
        },
    )

    def fail_write(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic failure after PostgreSQL state writes")

    def assert_rows(expected: int) -> None:
        with sessions() as session:
            for model in (TelemetryEvent, ZoneLatestState, BuildingLatestState, HvacDecision):
                rows = session.scalars(select(model).where(model.building_id == building)).all()
                assert len(rows) == expected

    try:
        with TestClient(ingestion.app) as client, TestClient(query.app) as reader:
            assert client.post("/telemetry", json=payload).status_code == 202
            message = queue.receive_messages()[0].body
            event = {
                "Records": [{"messageId": message.message_id, "body": message.model_dump_json()}]
            }
            with monkeypatch.context() as patch:
                patch.setattr(processor, "create_decision", fail_write)
                assert process_event(event, settings=settings, session_factory=sessions) == {
                    "batchItemFailures": [{"itemIdentifier": message.message_id}]
                }
            assert_rows(0)
            assert process_event(event, settings=settings, session_factory=sessions) == {
                "batchItemFailures": []
            }
            assert_rows(1)
            # An HTTP retry generates a new event ID, but the logical measurement is the same.
            assert client.post("/telemetry", json=payload).status_code == 202
            duplicate = queue.receive_messages()[0].body
            assert duplicate.payload.event_id != message.payload.event_id
            mixed_batch = {
                "Records": [
                    {"messageId": duplicate.message_id, "body": duplicate.model_dump_json()},
                    {"messageId": "synthetic-poison", "body": '{"broken": true}'},
                ]
            }
            assert process_event(mixed_batch, settings=settings, session_factory=sessions) == {
                "batchItemFailures": [{"itemIdentifier": "synthetic-poison"}]
            }
            assert_rows(1)
            response = reader.get(f"/buildings/{building}/state")
            assert response.status_code == 200
            assert response.json()["zone_count"] == 1
            assert len(reader.get(f"/buildings/{building}/events").json()["items"]) == 1
            assert len(reader.get(f"/buildings/{building}/decisions").json()["items"]) == 1
    finally:
        engine.dispose()
