from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HvacMode(StrEnum):
    OFF = "off"
    HEATING = "heating"
    COOLING = "cooling"
    FAN_ONLY = "fan_only"
    VENTILATION = "ventilation"
    AUTO = "auto"


class TelemetryIn(BaseModel):
    simulation_id: str | None = None
    reading_sequence: int | None = None
    building_id: str = Field(min_length=1)
    zone_id: str = Field(min_length=1)
    timestamp: datetime
    temperature_c: float
    humidity_pct: float
    occupancy: int
    co2_ppm: int
    hvac_mode: HvacMode
    airflow_pct: int

    @model_validator(mode="after")
    def validate_thermal_reference(self) -> TelemetryIn:
        if (self.simulation_id is None) != (self.reading_sequence is None):
            raise ValueError("simulation_id and reading_sequence must be supplied together")
        if self.reading_sequence is not None and self.reading_sequence < 1:
            raise ValueError("reading_sequence must be positive")
        return self

    model_config = ConfigDict(use_enum_values=True)


def build_idempotency_key(building_id: str, zone_id: str, event_timestamp: datetime) -> str:
    return f"{building_id}:{zone_id}:{event_timestamp.isoformat()}"
