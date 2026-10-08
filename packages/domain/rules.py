from __future__ import annotations

from dataclasses import dataclass

from packages.schemas.queue import QueuePayload


@dataclass(frozen=True)
class DecisionRecommendation:
    decision_type: str
    reason_code: str
    reason_text: str
    recommended_hvac_mode: str | None = None
    recommended_airflow_pct: int | None = None
    confidence: float | None = None


def derive_anomaly_flags(payload: QueuePayload) -> list[str]:
    flags: list[str] = []
    if payload.temperature_c > 25.5:
        flags.append("high_temp")
    if payload.co2_ppm > 1000:
        flags.append("high_co2")
    if payload.occupancy == 0 and payload.airflow_pct > 60:
        flags.append("low_occupancy_waste")
    return flags


def maybe_generate_decision(payload: QueuePayload) -> DecisionRecommendation | None:
    if payload.temperature_c > 25.5 and payload.occupancy > 0:
        return DecisionRecommendation(
            decision_type="adjust_airflow",
            reason_code="temperature_above_target_and_occupancy_present",
            reason_text="High temperature with occupancy requires additional cooling airflow.",
            recommended_hvac_mode="cooling",
            recommended_airflow_pct=min(100, payload.airflow_pct + 10),
            confidence=0.92,
        )

    if payload.co2_ppm > 1000:
        return DecisionRecommendation(
            decision_type="increase_ventilation",
            reason_code="co2_above_threshold",
            reason_text="Elevated CO2 level requires higher ventilation.",
            recommended_hvac_mode="ventilation",
            recommended_airflow_pct=min(100, payload.airflow_pct + 15),
            confidence=0.89,
        )

    if (
        payload.occupancy == 0
        and 21.0 <= payload.temperature_c <= 24.0
        and payload.airflow_pct > 60
    ):
        return DecisionRecommendation(
            decision_type="reduce_airflow",
            reason_code="low_occupancy_waste",
            reason_text="Airflow is higher than necessary for an unoccupied stable zone.",
            recommended_hvac_mode=payload.hvac_mode,
            recommended_airflow_pct=max(20, payload.airflow_pct - 20),
            confidence=0.86,
        )

    return None
