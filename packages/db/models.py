from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_telemetry_events_idempotency_key"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    building_id: Mapped[str] = mapped_column(String(128), index=True)
    zone_id: Mapped[str] = mapped_column(String(128), index=True)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    temperature_c: Mapped[float] = mapped_column(Float)
    humidity_pct: Mapped[float] = mapped_column(Float)
    occupancy: Mapped[int] = mapped_column(Integer)
    co2_ppm: Mapped[int] = mapped_column(Integer)
    hvac_mode: Mapped[str] = mapped_column(String(64))
    airflow_pct: Mapped[int] = mapped_column(Integer)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    anomaly_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    processing_status: Mapped[str] = mapped_column(String(32), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)


class ZoneLatestState(Base):
    __tablename__ = "zone_latest_state"

    building_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    zone_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_event_id: Mapped[str] = mapped_column(String(64))
    last_processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    temperature_c: Mapped[float] = mapped_column(Float)
    humidity_pct: Mapped[float] = mapped_column(Float)
    occupancy: Mapped[int] = mapped_column(Integer)
    co2_ppm: Mapped[int] = mapped_column(Integer)
    hvac_mode: Mapped[str] = mapped_column(String(64))
    airflow_pct: Mapped[int] = mapped_column(Integer)
    anomaly_flags: Mapped[list[str]] = mapped_column(JSON, default=list)


class BuildingLatestState(Base):
    __tablename__ = "building_latest_state"

    building_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    zone_count: Mapped[int] = mapped_column(Integer)
    avg_temperature_c: Mapped[float] = mapped_column(Float)
    avg_humidity_pct: Mapped[float] = mapped_column(Float)
    total_occupancy: Mapped[int] = mapped_column(Integer)
    dominant_hvac_mode: Mapped[str] = mapped_column(String(64))
    active_alerts: Mapped[int] = mapped_column(Integer)


class HvacDecision(Base):
    __tablename__ = "hvac_decisions"

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    building_id: Mapped[str] = mapped_column(String(128), index=True)
    zone_id: Mapped[str] = mapped_column(String(128), index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decision_type: Mapped[str] = mapped_column(String(64))
    recommended_hvac_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recommended_airflow_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason_code: Mapped[str] = mapped_column(String(128))
    reason_text: Mapped[str] = mapped_column(String(512))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)


class ProcessingObservation(Base):
    __tablename__ = "processing_observations"

    observation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    event_id: Mapped[str] = mapped_column(String(64))
    envelope_id: Mapped[str] = mapped_column(String(64))
    transport_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    original_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    runtime: Mapped[str] = mapped_column(String(64))
    process_id: Mapped[int] = mapped_column(Integer)
    release_revision: Mapped[str] = mapped_column(String(128))
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
