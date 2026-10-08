from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime

import httpx

DEFAULT_INGESTION_BASE_URL = "http://localhost:8000"
DEFAULT_QUERY_BASE_URL = "http://localhost:8001"
DEFAULT_BUILDING_ID = "building-001"
DEFAULT_ZONE_ID = "floor-02-east"
DEFAULT_ATTEMPTS = 20
DEFAULT_DELAY_SECONDS = 1.0


def get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def main() -> None:
    ingestion_base_url = os.getenv("INGESTION_BASE_URL", DEFAULT_INGESTION_BASE_URL).rstrip("/")
    query_base_url = os.getenv("QUERY_BASE_URL", DEFAULT_QUERY_BASE_URL).rstrip("/")
    staging_token = os.getenv("STAGING_AUTH_TOKEN")
    building_id = os.getenv("SMOKE_BUILDING_ID", DEFAULT_BUILDING_ID)
    zone_id = os.getenv("SMOKE_ZONE_ID", DEFAULT_ZONE_ID)
    attempts = get_env_int("SMOKE_QUERY_ATTEMPTS", DEFAULT_ATTEMPTS)
    delay_seconds = get_env_float("SMOKE_QUERY_DELAY_SECONDS", DEFAULT_DELAY_SECONDS)
    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    payload = {
        "building_id": building_id,
        "zone_id": zone_id,
        "timestamp": timestamp,
        "temperature_c": 26.4,
        "humidity_pct": 43.1,
        "occupancy": 12,
        "co2_ppm": 780,
        "hvac_mode": "cooling",
        "airflow_pct": 65,
    }
    protected_headers = {"content-type": "application/json"}
    if staging_token:
        protected_headers["X-Staging-Token"] = staging_token

    with httpx.Client(timeout=10.0) as client:
        client.get(f"{ingestion_base_url}/health").raise_for_status()
        client.get(f"{query_base_url}/health").raise_for_status()

        ingest_response = client.post(
            f"{ingestion_base_url}/telemetry",
            headers=protected_headers,
            json=payload,
        )
        ingest_response.raise_for_status()
        ingest_payload = ingest_response.json()
        if ingest_payload.get("status") != "accepted":
            raise RuntimeError(f"Unexpected ingestion response: {json.dumps(ingest_payload)}")

        state_response: httpx.Response | None = None
        for _ in range(attempts):
            candidate = client.get(
                f"{query_base_url}/buildings/{building_id}/state",
                headers=protected_headers,
            )
            if candidate.status_code == 200:
                state_response = candidate
                break
            time.sleep(delay_seconds)

        if state_response is None:
            raise RuntimeError("State query did not succeed within the smoke timeout")

        state_payload = state_response.json()
        if state_payload.get("building_id") != building_id:
            raise RuntimeError(f"Unexpected state payload: {json.dumps(state_payload)}")

        events_response = client.get(
            f"{query_base_url}/buildings/{building_id}/events",
            headers=protected_headers,
            params={"limit": 5},
        )
        events_response.raise_for_status()
        events_payload = events_response.json()
        items = events_payload.get("items")
        if not isinstance(items, list) or not items:
            raise RuntimeError(f"Unexpected events payload: {json.dumps(events_payload)}")

    print("Smoke checks passed")


if __name__ == "__main__":
    main()
