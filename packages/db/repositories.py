from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.db.models import BuildingLatestState, HvacDecision, TelemetryEvent, ZoneLatestState
from packages.domain.rules import DecisionRecommendation
from packages.schemas.queue import QueuePayload


def event_exists(session: Session, idempotency_key: str) -> bool:
    return (
        session.scalar(
            select(TelemetryEvent.event_id).where(TelemetryEvent.idempotency_key == idempotency_key)
        )
        is not None
    )


def create_telemetry_event(
    session: Session,
    payload: QueuePayload,
    *,
    idempotency_key: str,
    processing_status: str,
    anomaly_flags: list[str],
    processed_at: datetime | None,
    correlation_id: str | None = None,
) -> TelemetryEvent:
    event = TelemetryEvent(
        event_id=payload.event_id,
        correlation_id=correlation_id,
        building_id=payload.building_id,
        zone_id=payload.zone_id,
        event_timestamp=payload.event_timestamp,
        received_at=payload.received_at,
        processed_at=processed_at,
        temperature_c=payload.temperature_c,
        humidity_pct=payload.humidity_pct,
        occupancy=payload.occupancy,
        co2_ppm=payload.co2_ppm,
        hvac_mode=payload.hvac_mode,
        airflow_pct=payload.airflow_pct,
        raw_payload=payload.raw_payload,
        anomaly_flags=anomaly_flags,
        processing_status=processing_status,
        idempotency_key=idempotency_key,
    )
    session.add(event)
    session.flush()
    return event


def upsert_zone_latest_state(
    session: Session,
    payload: QueuePayload,
    *,
    processed_at: datetime,
    anomaly_flags: list[str],
) -> ZoneLatestState:
    current = session.get(
        ZoneLatestState, {"building_id": payload.building_id, "zone_id": payload.zone_id}
    )
    if current is None:
        current = ZoneLatestState(
            building_id=payload.building_id,
            zone_id=payload.zone_id,
            last_event_id=payload.event_id,
            last_processed_at=processed_at,
            event_timestamp=payload.event_timestamp,
            temperature_c=payload.temperature_c,
            humidity_pct=payload.humidity_pct,
            occupancy=payload.occupancy,
            co2_ppm=payload.co2_ppm,
            hvac_mode=payload.hvac_mode,
            airflow_pct=payload.airflow_pct,
            anomaly_flags=anomaly_flags,
        )
        session.add(current)
        session.flush()
        return current

    if payload.event_timestamp >= current.event_timestamp:
        current.last_event_id = payload.event_id
        current.last_processed_at = processed_at
        current.event_timestamp = payload.event_timestamp
        current.temperature_c = payload.temperature_c
        current.humidity_pct = payload.humidity_pct
        current.occupancy = payload.occupancy
        current.co2_ppm = payload.co2_ppm
        current.hvac_mode = payload.hvac_mode
        current.airflow_pct = payload.airflow_pct
        current.anomaly_flags = anomaly_flags
        session.flush()
    return current


def recompute_building_latest_state(
    session: Session,
    building_id: str,
    *,
    processed_at: datetime,
) -> BuildingLatestState:
    rows = session.scalars(
        select(ZoneLatestState).where(ZoneLatestState.building_id == building_id)
    ).all()
    zone_count = len(rows)
    avg_temperature = sum(row.temperature_c for row in rows) / zone_count if zone_count else 0.0
    avg_humidity = sum(row.humidity_pct for row in rows) / zone_count if zone_count else 0.0
    total_occupancy = sum(row.occupancy for row in rows)
    active_alerts = sum(len(row.anomaly_flags) for row in rows)
    dominant_mode = (
        Counter(row.hvac_mode for row in rows).most_common(1)[0][0] if rows else "unknown"
    )

    current = session.get(BuildingLatestState, building_id)
    if current is None:
        current = BuildingLatestState(
            building_id=building_id,
            last_processed_at=processed_at,
            zone_count=zone_count,
            avg_temperature_c=avg_temperature,
            avg_humidity_pct=avg_humidity,
            total_occupancy=total_occupancy,
            dominant_hvac_mode=dominant_mode,
            active_alerts=active_alerts,
        )
        session.add(current)
    else:
        current.last_processed_at = processed_at
        current.zone_count = zone_count
        current.avg_temperature_c = avg_temperature
        current.avg_humidity_pct = avg_humidity
        current.total_occupancy = total_occupancy
        current.dominant_hvac_mode = dominant_mode
        current.active_alerts = active_alerts

    session.flush()
    return current


def create_decision(
    session: Session,
    payload: QueuePayload,
    decision: DecisionRecommendation,
    *,
    generated_at: datetime,
) -> HvacDecision:
    item = HvacDecision(
        decision_id=str(uuid4()),
        event_id=payload.event_id,
        building_id=payload.building_id,
        zone_id=payload.zone_id,
        generated_at=generated_at,
        decision_type=decision.decision_type,
        recommended_hvac_mode=decision.recommended_hvac_mode,
        recommended_airflow_pct=decision.recommended_airflow_pct,
        reason_code=decision.reason_code,
        reason_text=decision.reason_text,
        confidence=decision.confidence,
        applied=False,
    )
    session.add(item)
    session.flush()
    return item


def list_buildings(session: Session) -> list[BuildingLatestState]:
    return list(
        session.scalars(select(BuildingLatestState).order_by(BuildingLatestState.building_id)).all()
    )


def get_building_state(session: Session, building_id: str) -> BuildingLatestState | None:
    return session.get(BuildingLatestState, building_id)


def list_zones(session: Session, building_id: str) -> list[ZoneLatestState]:
    return list(
        session.scalars(
            select(ZoneLatestState)
            .where(ZoneLatestState.building_id == building_id)
            .order_by(ZoneLatestState.zone_id)
        ).all()
    )


def list_decisions(session: Session, building_id: str, limit: int = 20) -> list[HvacDecision]:
    return list(
        session.scalars(
            select(HvacDecision)
            .where(HvacDecision.building_id == building_id)
            .order_by(HvacDecision.generated_at.desc())
            .limit(limit)
        ).all()
    )


def list_events(session: Session, building_id: str, limit: int = 50) -> list[TelemetryEvent]:
    return list(
        session.scalars(
            select(TelemetryEvent)
            .where(TelemetryEvent.building_id == building_id)
            .order_by(TelemetryEvent.event_timestamp.desc())
            .limit(limit)
        ).all()
    )


def utc_now() -> datetime:
    return datetime.now(UTC)
