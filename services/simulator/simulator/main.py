from __future__ import annotations

import argparse
import random
import time
from datetime import UTC, datetime, timedelta

import httpx

from packages.config import get_settings
from packages.logging import configure_logging, get_logger


def build_demo_events() -> list[dict[str, object]]:
    base_time = datetime.now(UTC).replace(microsecond=0)
    return [
        {
            "building_id": "building-001",
            "zone_id": "floor-02-east",
            "timestamp": (base_time + timedelta(seconds=1)).isoformat(),
            "temperature_c": 26.4,
            "humidity_pct": 43.1,
            "occupancy": 12,
            "co2_ppm": 780,
            "hvac_mode": "cooling",
            "airflow_pct": 65,
        },
        {
            "building_id": "building-001",
            "zone_id": "floor-02-west",
            "timestamp": (base_time + timedelta(seconds=2)).isoformat(),
            "temperature_c": 23.0,
            "humidity_pct": 44.0,
            "occupancy": 8,
            "co2_ppm": 1205,
            "hvac_mode": "ventilation",
            "airflow_pct": 55,
        },
        {
            "building_id": "building-002",
            "zone_id": "lobby",
            "timestamp": (base_time + timedelta(seconds=3)).isoformat(),
            "temperature_c": 22.2,
            "humidity_pct": 41.0,
            "occupancy": 0,
            "co2_ppm": 500,
            "hvac_mode": "cooling",
            "airflow_pct": 75,
        },
    ]


def build_random_event() -> dict[str, object]:
    building_id = random.choice(["building-001", "building-002"])
    zone_id = random.choice(["floor-01", "floor-02-east", "floor-02-west", "lobby"])
    return {
        "building_id": building_id,
        "zone_id": zone_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "temperature_c": round(random.uniform(20.0, 27.0), 2),
        "humidity_pct": round(random.uniform(35.0, 50.0), 2),
        "occupancy": random.randint(0, 20),
        "co2_ppm": random.randint(450, 1250),
        "hvac_mode": random.choice(["cooling", "heating", "ventilation", "auto"]),
        "airflow_pct": random.randint(20, 90),
    }


def post_event(client: httpx.Client, base_url: str, payload: dict[str, object]) -> None:
    response = client.post(f"{base_url}/telemetry", json=payload, timeout=10.0)
    response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["batch", "continuous"], default="batch")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("simulator", settings.log_level)
    logger = get_logger("simulator")

    with httpx.Client() as client:
        if args.mode == "batch":
            for payload in build_demo_events():
                post_event(client, settings.ingestion_base_url, payload)
                logger.info(
                    "simulator_event_sent",
                    extra={
                        "building_id": payload["building_id"],
                        "zone_id": payload["zone_id"],
                        "outcome": "sent",
                    },
                )
                time.sleep(settings.simulator_batch_interval_seconds)
            return

        while True:
            payload = build_random_event()
            post_event(client, settings.ingestion_base_url, payload)
            logger.info(
                "simulator_event_sent",
                extra={
                    "building_id": payload["building_id"],
                    "zone_id": payload["zone_id"],
                    "outcome": "sent",
                },
            )
            time.sleep(settings.simulator_continuous_interval_seconds)


if __name__ == "__main__":
    main()
