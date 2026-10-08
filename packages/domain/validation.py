from __future__ import annotations

from datetime import UTC, datetime, timedelta

from packages.config import Settings
from packages.schemas.queue import QueuePayload


class BusinessValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_business_payload(payload: QueuePayload, settings: Settings) -> None:
    errors: list[str] = []
    if not (-50.0 <= payload.temperature_c <= 80.0):
        errors.append("temperature_c_out_of_range")
    if not (0.0 <= payload.humidity_pct <= 100.0):
        errors.append("humidity_pct_out_of_range")
    if payload.occupancy < 0:
        errors.append("occupancy_negative")
    if not (0 <= payload.airflow_pct <= 100):
        errors.append("airflow_pct_out_of_range")

    now = datetime.now(UTC)
    if payload.event_timestamp > now + timedelta(minutes=settings.event_max_future_minutes):
        errors.append("event_timestamp_too_far_in_future")
    if payload.event_timestamp < now - timedelta(days=settings.event_max_past_days):
        errors.append("event_timestamp_too_old")

    if errors:
        raise BusinessValidationError(errors)
