from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from packages.config import Settings
from packages.db.models import TelemetryEvent
from packages.db.repositories import (
    create_decision,
    create_telemetry_event,
    recompute_building_latest_state,
    upsert_zone_latest_state,
    utc_now,
)
from packages.domain import (
    BusinessValidationError,
    derive_anomaly_flags,
    maybe_generate_decision,
    normalize_payload,
    validate_business_payload,
)
from packages.logging import get_logger
from packages.queue.base import QueueDelivery
from packages.schemas.queue import QueueEnvelope
from packages.thermal.service import process_reading, validate_reading
from packages.tracking.journal import Attempt, observe_independently, observe_result

logger = get_logger("processing-worker")


@dataclass(frozen=True)
class ProcessResult:
    outcome: str
    decision_created: bool = False


@dataclass(frozen=True)
class DeliveryProcessingResult:
    outcome: str
    acknowledged: bool
    retryable: bool
    decision_created: bool = False


def process_queue_message(
    message: QueueEnvelope,
    session_factory: sessionmaker[Session],
    settings: Settings,
    attempt: Attempt | None = None,
) -> ProcessResult:
    normalized = normalize_payload(message.payload)
    outcome = "processed"
    try:
        validate_business_payload(normalized, settings)
    except BusinessValidationError:
        outcome = "rejected"

    with session_factory() as session:
        thermal_command = None
        if message.payload.simulation_id is not None:
            validate_reading(session, message.payload)
        original = session.scalar(
            select(TelemetryEvent).where(TelemetryEvent.idempotency_key == message.idempotency_key)
        )
        if original is not None:
            if message.payload.simulation_id is not None:
                process_reading(
                    session,
                    message.payload.model_copy(
                        update={
                            "event_id": original.event_id,
                        }
                    ),
                    controller_enabled=False,
                )
            observe_result(session, attempt, "duplicate", original.event_id)
            session.commit()
            return ProcessResult(outcome="duplicate")

        if message.payload.simulation_id is not None:
            thermal_command = process_reading(
                session, message.payload, controller_enabled=outcome == "processed"
            )
        processed_at = utc_now()
        anomaly_flags = derive_anomaly_flags(normalized) if outcome == "processed" else []
        create_telemetry_event(
            session,
            normalized,
            idempotency_key=message.idempotency_key,
            processing_status=outcome,
            anomaly_flags=anomaly_flags,
            processed_at=processed_at,
            correlation_id=message.correlation_id,
        )
        decision = None
        if outcome == "processed":
            upsert_zone_latest_state(
                session,
                normalized,
                processed_at=processed_at,
                anomaly_flags=anomaly_flags,
            )
            recompute_building_latest_state(
                session,
                normalized.building_id,
                processed_at=processed_at,
            )
            decision = (
                None if message.payload.simulation_id else maybe_generate_decision(normalized)
            )
            if thermal_command is not None:
                from packages.domain.rules import DecisionRecommendation

                decision = DecisionRecommendation(
                    decision_type="thermal_control",
                    recommended_hvac_mode=thermal_command.command_type.split(".")[0]
                    if thermal_command.command_type.endswith(".start")
                    else "off",
                    recommended_airflow_pct=None,
                    reason_code="thermal_start"
                    if thermal_command.command_type.endswith(".start")
                    else "thermal_target",
                    reason_text="Authoritative thermal controller command",
                    confidence=1.0,
                )
            if decision is not None:
                stored_decision = create_decision(
                    session, normalized, decision, generated_at=processed_at
                )
                if thermal_command is not None:
                    thermal_command.decision_id = stored_decision.decision_id
        observe_result(session, attempt, outcome, normalized.event_id)
        session.commit()
    return ProcessResult(outcome=outcome, decision_created=decision is not None)


def process_delivery(
    delivery: QueueDelivery,
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> DeliveryProcessingResult:
    return process_envelope(delivery.body, session_factory, settings, delivery.transport_message_id)


def process_envelope(
    message: QueueEnvelope,
    session_factory: sessionmaker[Session],
    settings: Settings,
    transport_message_id: str | None = None,
) -> DeliveryProcessingResult:
    attempt = Attempt(message, settings, transport_message_id)
    observe_independently(session_factory, attempt, "started")
    try:
        result = process_queue_message(message, session_factory, settings, attempt)
    except Exception:
        observe_independently(session_factory, attempt, "failed")
        logger.exception(
            "telemetry_processing_failed",
            extra={
                "event_id": message.payload.event_id,
                "building_id": message.payload.building_id,
                "zone_id": message.payload.zone_id,
                "correlation_id": message.correlation_id,
                "outcome": "failed",
            },
        )
        return DeliveryProcessingResult(outcome="failed", acknowledged=False, retryable=True)

    # Logging after commit must never turn a persisted result into a retryable failure.
    try:
        logger.info(
            f"telemetry_{result.outcome}",
            extra={
                "event_id": message.payload.event_id,
                "building_id": message.payload.building_id,
                "zone_id": message.payload.zone_id,
                "correlation_id": message.correlation_id,
                "outcome": result.outcome,
            },
        )
    except Exception:
        pass
    return DeliveryProcessingResult(
        outcome=result.outcome,
        acknowledged=True,
        retryable=False,
        decision_created=result.decision_created,
    )


def process_sqs_record(
    record: Mapping[str, Any],
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> tuple[str, DeliveryProcessingResult]:
    record_id = str(record.get("messageId", "unknown"))

    try:
        body = record["body"]
        if not isinstance(body, str):
            raise TypeError("SQS message body must be a JSON string")
        message = QueueEnvelope.model_validate_json(body)
    except Exception:
        logger.exception(
            "telemetry_batch_record_invalid",
            extra={"correlation_id": record_id, "outcome": "failed"},
        )
        return record_id, DeliveryProcessingResult(
            outcome="failed", acknowledged=False, retryable=True
        )

    return record_id, process_envelope(message, session_factory, settings, record_id)


def process_sqs_event(
    event: Mapping[str, Any],
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> dict[str, list[dict[str, str]]]:
    failures: list[dict[str, str]] = []
    records = event.get("Records", [])
    if not isinstance(records, list):
        raise TypeError("SQS event Records must be a list")

    for record in records:
        record_id, result = process_sqs_record(record, session_factory, settings)
        if result.retryable:
            failures.append({"itemIdentifier": record_id})

    return {"batchItemFailures": failures}
