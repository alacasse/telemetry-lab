from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str


class TransportReceipt(BaseModel):
    message_id: str
    source: str
    runtime: str
    observed_at: datetime
    fetched_at: datetime
    quality: str = "observed"
    scope: str = "send"


class AcceptedTelemetryResponse(BaseModel):
    event_id: str
    status: str
    correlation_id: str
    envelope_id: str
    transport_receipt: TransportReceipt


class BuildingItem(BaseModel):
    building_id: str


class BuildingListResponse(BaseModel):
    items: list[BuildingItem]


class BuildingStateResponse(BaseModel):
    building_id: str
    last_processed_at: datetime | None
    zone_count: int
    avg_temperature_c: float
    avg_humidity_pct: float
    total_occupancy: int
    dominant_hvac_mode: str
    active_alerts: int


class ZoneStateItem(BaseModel):
    zone_id: str
    last_processed_at: datetime
    temperature_c: float
    humidity_pct: float
    occupancy: int
    co2_ppm: int
    hvac_mode: str
    airflow_pct: int
    anomaly_flags: list[str]


class ZoneListResponse(BaseModel):
    building_id: str
    items: list[ZoneStateItem]


class DecisionItem(BaseModel):
    decision_id: str
    zone_id: str
    generated_at: datetime
    decision_type: str
    recommended_hvac_mode: str | None
    recommended_airflow_pct: int | None
    reason: str


class DecisionListResponse(BaseModel):
    building_id: str
    items: list[DecisionItem]


class EventItem(BaseModel):
    event_id: str
    zone_id: str
    processed_at: datetime | None
    temperature_c: float
    humidity_pct: float
    occupancy: int
    co2_ppm: int
    hvac_mode: str
    airflow_pct: int
    processing_status: str


class EventListResponse(BaseModel):
    building_id: str
    items: list[EventItem]
