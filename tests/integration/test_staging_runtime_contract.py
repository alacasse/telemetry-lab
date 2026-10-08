from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ingestion_service.main import (
    app as ingestion_app,
)
from ingestion_service.main import (
    clear_dependency_caches as clear_ingestion_dependency_caches,
)
from ingestion_service.main import (
    get_queue_client_dependency,
)
from ingestion_service.main import (
    get_settings_dependency as get_ingestion_settings_dependency,
)
from processing_worker.main import run_worker_iteration
from query_service.main import (
    app as query_app,
)
from query_service.main import (
    clear_dependency_caches as clear_query_dependency_caches,
)
from query_service.main import (
    get_session_factory_dependency,
)
from query_service.main import (
    get_settings_dependency as get_query_settings_dependency,
)
from sqlalchemy.orm import sessionmaker

from packages.config import Settings, get_settings
from packages.db.base import Base
from packages.db.session import get_engine
from packages.queue.memory import InMemoryQueueClient

EXPECTED_TOKEN = "staging-token"


def build_staging_settings() -> Settings:
    return Settings(
        APP_ENV="staging",
        DATABASE_SECRET_ARN="arn:aws:secretsmanager:ca-central-1:123:secret:db",
        STAGING_AUTH_SECRET_ARN="arn:aws:secretsmanager:ca-central-1:123:secret:auth",
        RELEASE_REVISION="abc123",
    )


def test_staging_contract_preserves_public_routes_and_token_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Settings, "resolve_staging_auth_token", lambda self: EXPECTED_TOKEN)
    staging_settings = build_staging_settings()
    db_path = tmp_path / "staging-contract.sqlite"
    worker_settings = Settings(
        _env_file=None, QUEUE_BACKEND="inmemory", DATABASE_URL=f"sqlite:///{db_path}"
    )
    engine = get_engine(worker_settings)
    Base.metadata.create_all(engine)
    session_factory: sessionmaker = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
    )
    queue_client = InMemoryQueueClient()

    ingestion_app.dependency_overrides[get_ingestion_settings_dependency] = lambda: staging_settings
    ingestion_app.dependency_overrides[get_queue_client_dependency] = lambda: queue_client
    query_app.dependency_overrides[get_query_settings_dependency] = lambda: staging_settings
    query_app.dependency_overrides[get_session_factory_dependency] = lambda: session_factory

    try:
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

        assert ingestion_client.get("/health").status_code == 200
        assert query_client.get("/health").status_code == 200
        assert ingestion_client.post("/telemetry", json=payload).status_code == 401
        assert query_client.get("/buildings").status_code == 401

        response = ingestion_client.post(
            "/telemetry",
            headers={"X-Staging-Token": EXPECTED_TOKEN},
            json=payload,
        )
        assert response.status_code == 202

        processed_count = run_worker_iteration(queue_client, session_factory, worker_settings)
        assert processed_count == 1

        buildings_response = query_client.get(
            "/buildings",
            headers={"X-Staging-Token": EXPECTED_TOKEN},
        )
        assert buildings_response.status_code == 200
        assert buildings_response.json()["items"] == [{"building_id": "building-001"}]
    finally:
        ingestion_app.dependency_overrides.clear()
        query_app.dependency_overrides.clear()
        clear_ingestion_dependency_caches()
        clear_query_dependency_caches()
        get_settings.cache_clear()
