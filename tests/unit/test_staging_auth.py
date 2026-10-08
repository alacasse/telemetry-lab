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
from packages.db.models import BuildingLatestState
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


def test_health_routes_remain_public_in_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Settings, "resolve_staging_auth_token", lambda self: EXPECTED_TOKEN)
    settings = build_staging_settings()
    queue_client = InMemoryQueueClient()
    ingestion_app.dependency_overrides[get_ingestion_settings_dependency] = lambda: settings
    ingestion_app.dependency_overrides[get_queue_client_dependency] = lambda: queue_client
    query_app.dependency_overrides[get_query_settings_dependency] = lambda: settings

    try:
        assert TestClient(ingestion_app).get("/health").status_code == 200
        assert TestClient(query_app).get("/health").status_code == 200
    finally:
        ingestion_app.dependency_overrides.clear()
        query_app.dependency_overrides.clear()
        clear_ingestion_dependency_caches()
        clear_query_dependency_caches()
        get_settings.cache_clear()


def test_staging_token_protects_ingestion_and_query_routes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Settings, "resolve_staging_auth_token", lambda self: EXPECTED_TOKEN)
    settings = build_staging_settings()
    queue_client = InMemoryQueueClient()
    db_path = tmp_path / "staging-auth.sqlite"
    local_db_settings = Settings(_env_file=None, DATABASE_URL=f"sqlite:///{db_path}")
    engine = get_engine(local_db_settings)
    Base.metadata.create_all(engine)
    session_factory: sessionmaker = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
    )
    with session_factory() as session:
        session.add(
            BuildingLatestState(
                building_id="building-001",
                last_processed_at=datetime.now(UTC),
                zone_count=1,
                avg_temperature_c=22.0,
                avg_humidity_pct=40.0,
                total_occupancy=5,
                dominant_hvac_mode="cooling",
                active_alerts=0,
            )
        )
        session.commit()

    ingestion_app.dependency_overrides[get_ingestion_settings_dependency] = lambda: settings
    ingestion_app.dependency_overrides[get_queue_client_dependency] = lambda: queue_client
    query_app.dependency_overrides[get_query_settings_dependency] = lambda: settings
    query_app.dependency_overrides[get_session_factory_dependency] = lambda: session_factory

    try:
        ingestion_client = TestClient(ingestion_app)
        query_client = TestClient(query_app)
        payload = {
            "building_id": "building-001",
            "zone_id": "floor-02-east",
            "timestamp": datetime.now(UTC).isoformat(),
            "temperature_c": 26.4,
            "humidity_pct": 43.1,
            "occupancy": 12,
            "co2_ppm": 780,
            "hvac_mode": "cooling",
            "airflow_pct": 65,
        }

        assert ingestion_client.post("/telemetry", json=payload).status_code == 401
        assert query_client.get("/buildings").status_code == 401

        ingestion_response = ingestion_client.post(
            "/telemetry",
            headers={"X-Staging-Token": EXPECTED_TOKEN},
            json=payload,
        )
        query_response = query_client.get(
            "/buildings",
            headers={"X-Staging-Token": EXPECTED_TOKEN},
        )

        assert ingestion_response.status_code == 202
        assert query_response.status_code == 200
    finally:
        ingestion_app.dependency_overrides.clear()
        query_app.dependency_overrides.clear()
        clear_ingestion_dependency_caches()
        clear_query_dependency_caches()
        get_settings.cache_clear()
