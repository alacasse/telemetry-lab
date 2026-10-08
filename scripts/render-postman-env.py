from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = ROOT / "postman/telemetry-lab.aws.postman_environment.json"
DEFAULT_OUTPUT = ROOT / "dist/postman/aws-smoke.postman_environment.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render an AWS Postman environment file.")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--building-id", default="building-001")
    parser.add_argument("--zone-id", default="floor-02-east")
    parser.add_argument("--query-limit", default="20")
    parser.add_argument("--smoke-max-attempts", default="20")
    parser.add_argument("--staging-token", default="")
    parser.add_argument("--telemetry-timestamp")
    return parser.parse_args()


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


DEFAULT_VALUES = {
    "ingestionServicePrefix": "",
    "queryServicePrefix": "",
    "smokeStateAttempt": "0",
    "smokeEventAttempt": "0",
    "smokeDecisionAttempt": "0",
}


def upsert_value(values: list[dict[str, Any]], key: str, value: str) -> None:
    for item in values:
        if item.get("key") == key:
            item["value"] = value
            return
    values.append({"key": key, "value": value, "type": "default", "enabled": True})


def render_environment(
    template: dict[str, Any],
    *,
    base_url: str,
    building_id: str,
    zone_id: str,
    query_limit: str,
    smoke_max_attempts: str,
    telemetry_timestamp: str,
    staging_token: str,
) -> dict[str, Any]:
    values = template.setdefault("values", [])
    if not isinstance(values, list):
        raise RuntimeError("Postman environment template has invalid values payload")

    rendered = deepcopy(template)
    rendered_values = rendered["values"]
    replacements = {
        **DEFAULT_VALUES,
        "baseUrl": normalize_base_url(base_url),
        "telemetryBuildingId": building_id,
        "telemetryZoneId": zone_id,
        "telemetryTimestamp": telemetry_timestamp,
        "buildingId": building_id,
        "zoneId": zone_id,
        "queryLimit": query_limit,
        "smokeMaxAttempts": smoke_max_attempts,
        "stagingToken": staging_token,
        "createdEventId": "",
        "correlationId": "",
        "decisionId": "",
        "latestEventId": "",
        "latestProcessingStatus": "",
    }
    for key, value in replacements.items():
        upsert_value(rendered_values, key, value)
    return rendered


def get_default_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> None:
    args = parse_args()
    template = json.loads(args.template.resolve().read_text(encoding="utf-8"))
    if not isinstance(template, dict):
        raise RuntimeError("Postman environment template must be a JSON object")
    rendered = render_environment(
        template,
        base_url=args.base_url,
        building_id=args.building_id,
        zone_id=args.zone_id,
        query_limit=args.query_limit,
        smoke_max_attempts=args.smoke_max_attempts,
        telemetry_timestamp=args.telemetry_timestamp or get_default_timestamp(),
        staging_token=args.staging_token,
    )
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
