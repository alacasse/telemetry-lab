from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from ingestion_service.main import app as ingestion_app
from ingestion_service.main import get_queue_client_dependency, get_settings_dependency
from processing_worker.main import run_worker_iteration
from query_service.main import app as query_app
from query_service.main import (
    get_session_factory_dependency as get_query_session_factory_dependency,
)
from sqlalchemy.orm import sessionmaker

from packages.config import Settings
from packages.db.base import Base
from packages.db.session import get_engine
from packages.queue.memory import InMemoryQueueClient


def test_end_to_end_pipeline(tmp_path: Path) -> None:
    db_path = tmp_path / "telemetry_lab.sqlite"
    settings = Settings(
        _env_file=None,
        QUEUE_BACKEND="inmemory",
        DATABASE_URL=f"sqlite:///{db_path}",
    )
    engine = get_engine(settings)
    Base.metadata.create_all(engine)
    session_factory: sessionmaker = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )
    queue_client = InMemoryQueueClient()

    ingestion_app.dependency_overrides[get_settings_dependency] = lambda: settings
    ingestion_app.dependency_overrides[get_queue_client_dependency] = lambda: queue_client
    query_app.dependency_overrides[get_query_session_factory_dependency] = lambda: session_factory

    ingestion_client = TestClient(ingestion_app)
    query_client = TestClient(query_app)

    payload = {
        "building_id": "building-001",
        "zone_id": "floor-02-east",
        "timestamp": datetime.now(UTC).isoformat(),
        "temperature_c": 26.7,
        "humidity_pct": 44.0,
        "occupancy": 12,
        "co2_ppm": 780,
        "hvac_mode": "cooling",
        "airflow_pct": 65,
    }
    response = ingestion_client.post("/telemetry", json=payload)
    assert response.status_code == 202

    processed_count = run_worker_iteration(queue_client, session_factory, settings)
    assert processed_count == 1

    buildings_response = query_client.get("/buildings")
    assert buildings_response.status_code == 200
    assert buildings_response.json()["items"] == [{"building_id": "building-001"}]

    state_response = query_client.get("/buildings/building-001/state")
    assert state_response.status_code == 200
    assert state_response.json()["building_id"] == "building-001"

    zones_response = query_client.get("/buildings/building-001/zones")
    assert zones_response.status_code == 200
    assert len(zones_response.json()["items"]) == 1

    decisions_response = query_client.get("/buildings/building-001/decisions")
    assert decisions_response.status_code == 200
    assert decisions_response.json()["items"][0]["decision_type"] == "adjust_airflow"

    events_response = query_client.get("/buildings/building-001/events")
    assert events_response.status_code == 200
    assert events_response.json()["items"][0]["processing_status"] == "processed"
