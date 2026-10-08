from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, status

from packages.aws import annotate_trace, configure_tracing
from packages.config import Settings, get_settings
from packages.logging import configure_logging, get_logger
from packages.queue import get_queue_client
from packages.queue.base import QueueClient
from packages.schemas import (
    SCHEMA_VERSION,
    AcceptedTelemetryResponse,
    HealthResponse,
    QueueEnvelope,
    QueuePayload,
    TelemetryIn,
    build_idempotency_key,
)
from packages.schemas.api import TransportReceipt

configure_logging("ingestion-service", get_settings().log_level)
configure_tracing("ingestion-service", get_settings())
logger = get_logger("ingestion-service")
app = FastAPI(title="Telemetry Lab Ingestion Service")


def get_settings_dependency() -> Settings:
    return get_settings()


@lru_cache(maxsize=1)
def _get_cached_queue_client() -> QueueClient:
    return get_queue_client(get_settings())


def clear_dependency_caches() -> None:
    _get_cached_queue_client.cache_clear()


def get_queue_client_dependency() -> QueueClient:
    return _get_cached_queue_client()


def require_staging_auth(
    x_staging_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings_dependency),
) -> None:
    if not settings.is_staging:
        return

    expected_token = settings.resolve_staging_auth_token()
    if x_staging_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="staging_auth_required"
        )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="ingestion-service")


@app.post(
    "/telemetry", response_model=AcceptedTelemetryResponse, status_code=status.HTTP_202_ACCEPTED
)
def ingest_telemetry(
    telemetry: TelemetryIn,
    x_correlation_id: str | None = Header(default=None, min_length=1, max_length=128),
    _: None = Depends(require_staging_auth),
    settings: Settings = Depends(get_settings_dependency),
    queue_client: QueueClient = Depends(get_queue_client_dependency),
) -> AcceptedTelemetryResponse:
    event_id = str(uuid4())
    correlation_id = x_correlation_id or str(uuid4())
    received_at = datetime.now(UTC)
    idempotency_key = build_idempotency_key(
        telemetry.building_id, telemetry.zone_id, telemetry.timestamp
    )
    payload = QueuePayload(
        simulation_id=telemetry.simulation_id,
        reading_sequence=telemetry.reading_sequence,
        event_id=event_id,
        building_id=telemetry.building_id,
        zone_id=telemetry.zone_id,
        event_timestamp=telemetry.timestamp,
        received_at=received_at,
        temperature_c=telemetry.temperature_c,
        humidity_pct=telemetry.humidity_pct,
        occupancy=telemetry.occupancy,
        co2_ppm=telemetry.co2_ppm,
        hvac_mode=str(telemetry.hvac_mode),
        airflow_pct=telemetry.airflow_pct,
        raw_payload=telemetry.model_dump(mode="json"),
    )
    message = QueueEnvelope(
        message_id=str(uuid4()),
        message_type="telemetry.received",
        schema_version=SCHEMA_VERSION,
        published_at=received_at,
        source="ingestion-service",
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    annotate_trace(
        event_id=event_id,
        building_id=telemetry.building_id,
        zone_id=telemetry.zone_id,
        correlation_id=correlation_id,
    )

    try:
        transport_id = queue_client.publish_message(message)
        published_at = datetime.now(UTC)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        logger.exception(
            "queue_publication_failed",
            extra={
                "event_id": event_id,
                "building_id": telemetry.building_id,
                "zone_id": telemetry.zone_id,
                "correlation_id": correlation_id,
                "outcome": "failed",
            },
        )
        raise HTTPException(status_code=503, detail="queue_publication_failed") from exc

    logger.info(
        "telemetry_accepted",
        extra={
            "event_id": event_id,
            "building_id": telemetry.building_id,
            "zone_id": telemetry.zone_id,
            "correlation_id": correlation_id,
            "outcome": "accepted",
        },
    )
    return AcceptedTelemetryResponse(
        event_id=event_id,
        status="accepted",
        correlation_id=correlation_id,
        envelope_id=message.message_id,
        transport_receipt=TransportReceipt(
            message_id=transport_id,
            source="SQS.SendMessage" if settings.queue_backend == "sqs" else "memory.publish",
            runtime=("sqs-compatible-emulator" if settings.aws_endpoint_url else "aws-sqs")
            if settings.queue_backend == "sqs"
            else "inmemory",
            observed_at=published_at,
            fetched_at=published_at,
        ),
    )
