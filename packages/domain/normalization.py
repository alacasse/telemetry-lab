from __future__ import annotations

from packages.schemas.queue import QueuePayload


def normalize_payload(payload: QueuePayload) -> QueuePayload:
    return payload.model_copy(
        update={
            "temperature_c": round(payload.temperature_c, 2),
            "humidity_pct": round(payload.humidity_pct, 2),
            "hvac_mode": payload.hvac_mode.lower(),
            "airflow_pct": max(0, min(payload.airflow_pct, 100)),
        }
    )
