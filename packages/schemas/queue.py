from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

SCHEMA_VERSION = "1.0.0"


class QueuePayload(BaseModel):
    event_id: str
    simulation_id: str | None = None
    reading_sequence: int | None = None
    building_id: str
    zone_id: str
    event_timestamp: datetime
    received_at: datetime
    temperature_c: float
    humidity_pct: float
    occupancy: int
    co2_ppm: int
    hvac_mode: str
    airflow_pct: int
    raw_payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_thermal_reference(self) -> QueuePayload:
        if (self.simulation_id is None) != (self.reading_sequence is None):
            raise ValueError("simulation_id and reading_sequence must be supplied together")
        if self.reading_sequence is not None and self.reading_sequence < 1:
            raise ValueError("reading_sequence must be positive")
        return self

    model_config = ConfigDict(use_enum_values=True)


class QueueEnvelope(BaseModel):
    message_id: str
    message_type: str
    schema_version: str = SCHEMA_VERSION
    published_at: datetime
    source: str
    correlation_id: str
    idempotency_key: str
    payload: QueuePayload
