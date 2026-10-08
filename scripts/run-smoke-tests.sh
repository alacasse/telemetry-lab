#!/usr/bin/env bash
set -euo pipefail

INGESTION_BASE_URL="${INGESTION_BASE_URL:-http://localhost:8000}"
QUERY_BASE_URL="${QUERY_BASE_URL:-http://localhost:8001}"
SMOKE_BUILDING_ID="${SMOKE_BUILDING_ID:-building-001}"
SMOKE_ZONE_ID="${SMOKE_ZONE_ID:-floor-02-east}"
SMOKE_QUERY_ATTEMPTS="${SMOKE_QUERY_ATTEMPTS:-20}"
SMOKE_QUERY_DELAY_SECONDS="${SMOKE_QUERY_DELAY_SECONDS:-1}"

curl -fsS "${INGESTION_BASE_URL}/health" >/dev/null
curl -fsS "${QUERY_BASE_URL}/health" >/dev/null

TIMESTAMP="$(python - <<'PY'
from datetime import UTC, datetime
print(datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z'))
PY
)"

PAYLOAD="$(SMOKE_BUILDING_ID="${SMOKE_BUILDING_ID}" SMOKE_ZONE_ID="${SMOKE_ZONE_ID}" TIMESTAMP="${TIMESTAMP}" python - <<'PY'
import json
import os

print(json.dumps({
    "building_id": os.environ["SMOKE_BUILDING_ID"],
    "zone_id": os.environ["SMOKE_ZONE_ID"],
    "timestamp": os.environ["TIMESTAMP"],
    "temperature_c": 26.4,
    "humidity_pct": 43.1,
    "occupancy": 12,
    "co2_ppm": 780,
    "hvac_mode": "cooling",
    "airflow_pct": 65,
}))
PY
)"

TELEMETRY_RESPONSE="$(curl -fsS -X POST "${INGESTION_BASE_URL}/telemetry" -H 'content-type: application/json' -d "${PAYLOAD}")"
python - <<'PY' "${TELEMETRY_RESPONSE}"
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("status") != "accepted":
    raise SystemExit(f"unexpected ingestion response: {payload}")
PY

STATE_URL="${QUERY_BASE_URL}/buildings/${SMOKE_BUILDING_ID}/state"
EVENTS_URL="${QUERY_BASE_URL}/buildings/${SMOKE_BUILDING_ID}/events?limit=5"
STATE_RESPONSE=""

for _ in $(seq 1 "${SMOKE_QUERY_ATTEMPTS}"); do
  if STATE_RESPONSE="$(curl -fsS "${STATE_URL}" 2>/dev/null)"; then
    break
  fi
  sleep "${SMOKE_QUERY_DELAY_SECONDS}"
done

if [ -z "${STATE_RESPONSE}" ]; then
  printf 'state query did not succeed within the smoke timeout\n' >&2
  exit 1
fi

EVENTS_RESPONSE="$(curl -fsS "${EVENTS_URL}")"
python - <<'PY' "${STATE_RESPONSE}" "${EVENTS_RESPONSE}" "${SMOKE_BUILDING_ID}"
import json
import sys

state_payload = json.loads(sys.argv[1])
events_payload = json.loads(sys.argv[2])
building_id = sys.argv[3]

if state_payload.get("building_id") != building_id:
    raise SystemExit(f"unexpected state payload: {state_payload}")
items = events_payload.get("items")
if not isinstance(items, list) or not items:
    raise SystemExit(f"unexpected events payload: {events_payload}")
PY

printf 'Smoke checks passed\n'
