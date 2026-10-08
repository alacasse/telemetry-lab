from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from packages.config import Settings
from packages.domain import derive_anomaly_flags, maybe_generate_decision, validate_business_payload
from packages.schemas.queue import QueuePayload


def make_payload(**overrides: Any) -> QueuePayload:
    payload: dict[str, Any] = {
        "event_id": "evt-1",
        "building_id": "building-001",
        "zone_id": "zone-a",
        "event_timestamp": datetime.now(UTC),
        "received_at": datetime.now(UTC),
        "temperature_c": 26.0,
        "humidity_pct": 45.0,
        "occupancy": 10,
        "co2_ppm": 800,
        "hvac_mode": "cooling",
        "airflow_pct": 60,
        "raw_payload": {},
    }
    payload.update(overrides)
    return QueuePayload(**payload)


def test_business_validation_accepts_valid_payload() -> None:
    validate_business_payload(
        make_payload(), Settings(QUEUE_BACKEND="inmemory", DATABASE_URL="sqlite://")
    )


def test_anomaly_flags_detect_high_co2_and_waste() -> None:
    payload = make_payload(co2_ppm=1201, occupancy=0, airflow_pct=70)
    assert derive_anomaly_flags(payload) == ["high_temp", "high_co2", "low_occupancy_waste"]


def test_decision_rule_prefers_high_temperature_with_occupancy() -> None:
    payload = make_payload(temperature_c=26.5, occupancy=4, airflow_pct=50)
    decision = maybe_generate_decision(payload)
    assert decision is not None
    assert decision.decision_type == "adjust_airflow"
