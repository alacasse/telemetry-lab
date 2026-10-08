from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ingestion_service.lambda_handler import handler as ingestion_lambda_handler
from ingestion_service.main import (
    app as ingestion_app,
)
from ingestion_service.main import (
    clear_dependency_caches as clear_ingestion_dependency_caches,
)
from ingestion_service.main import (
    get_queue_client_dependency,
    get_settings_dependency,
)
from query_service.lambda_handler import handler as query_lambda_handler
from query_service.main import (
    app as query_app,
)
from query_service.main import (
    clear_dependency_caches as clear_query_dependency_caches,
)
from query_service.main import (
    get_session_factory_dependency,
)
from sqlalchemy.orm import sessionmaker

from packages.config import Settings, get_settings
from packages.db.base import Base
from packages.db.models import BuildingLatestState
from packages.db.session import get_engine
from packages.queue.memory import InMemoryQueueClient


def build_http_event(
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json"},
        "requestContext": {
            "stage": "$default",
            "requestId": "request-id",
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "pytest",
            },
            "routeKey": f"{method} {path}",
            "time": "10/Apr/2026:00:00:00 +0000",
            "timeEpoch": 0,
        },
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }


def test_ingestion_lambda_adapter_handles_health_and_telemetry() -> None:
    get_settings.cache_clear()
    clear_ingestion_dependency_caches()
    settings = Settings(_env_file=None, QUEUE_BACKEND="inmemory")
    queue_client = InMemoryQueueClient()
    ingestion_app.dependency_overrides[get_settings_dependency] = lambda: settings
    ingestion_app.dependency_overrides[get_queue_client_dependency] = lambda: queue_client

    try:
        health_response = ingestion_lambda_handler(build_http_event("GET", "/health"), None)
        assert health_response["statusCode"] == 200
        assert json.loads(health_response["body"]) == {
            "status": "ok",
            "service": "ingestion-service",
        }

        telemetry_response = ingestion_lambda_handler(
            build_http_event(
                "POST",
                "/telemetry",
                body={
                    "building_id": "building-001",
                    "zone_id": "floor-02-east",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "temperature_c": 26.4,
                    "humidity_pct": 43.1,
                    "occupancy": 12,
                    "co2_ppm": 780,
                    "hvac_mode": "cooling",
                    "airflow_pct": 65,
                },
            ),
            None,
        )
        assert telemetry_response["statusCode"] == 202
        assert queue_client.receive_messages(max_messages=10)
    finally:
        ingestion_app.dependency_overrides.clear()
        clear_ingestion_dependency_caches()
        get_settings.cache_clear()


def test_query_lambda_adapter_handles_health_and_query_route(tmp_path: Path) -> None:
    get_settings.cache_clear()
    clear_query_dependency_caches()
    db_path = tmp_path / "query.sqlite"
    settings = Settings(_env_file=None, DATABASE_URL=f"sqlite:///{db_path}")
    engine = get_engine(settings)
    Base.metadata.create_all(engine)
    session_factory: sessionmaker = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
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

    query_app.dependency_overrides[get_session_factory_dependency] = lambda: session_factory

    try:
        health_response = query_lambda_handler(build_http_event("GET", "/health"), None)
        assert health_response["statusCode"] == 200

        query_response = query_lambda_handler(
            build_http_event("GET", "/buildings/building-001/state"),
            None,
        )
        assert query_response["statusCode"] == 200
        body = json.loads(query_response["body"])
        assert body["building_id"] == "building-001"
        assert body["zone_count"] == 1
    finally:
        query_app.dependency_overrides.clear()
        clear_query_dependency_caches()
        get_settings.cache_clear()
