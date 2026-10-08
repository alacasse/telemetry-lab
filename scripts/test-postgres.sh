#!/bin/sh
# Uses a disposable container and dynamic loopback port; never touches Compose volumes.
set -eu
ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"
PYTHON=${PYTHON:-.venv/bin/python}
"$PYTHON" -c 'import pytest, psycopg, alembic, fastapi, sqlalchemy'
docker info >/dev/null
CONTAINER=$(docker run -d --rm -e POSTGRES_DB=telemetry_lab_test -e POSTGRES_USER=telemetry_lab \
  -e 'POSTGRES_PASSWORD=local-test:%@' -p 127.0.0.1::5432 postgres:16-alpine)
trap 'docker stop "$CONTAINER" >/dev/null' EXIT HUP INT TERM
attempt=0
until docker exec "$CONTAINER" pg_isready -U telemetry_lab -d telemetry_lab_test >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    printf 'PostgreSQL test container did not become ready\n' >&2
    exit 1
  fi
  sleep 1
done
PORT=$(docker port "$CONTAINER" 5432/tcp | sed 's/.*://')
export TELEMETRY_LAB_TEST_POSTGRES_URL="postgresql+psycopg://telemetry_lab:local-test%3A%25%40@127.0.0.1:$PORT/telemetry_lab_test"
# pg_isready inside the container can see the temporary bootstrap server.
# Verify the published TCP endpoint before starting migrations and process tests.
"$PYTHON" - <<'PY'
import os
import time

import psycopg

url = os.environ["TELEMETRY_LAB_TEST_POSTGRES_URL"].replace("postgresql+psycopg:", "postgresql:")
for attempt in range(30):
    try:
        with psycopg.connect(url, connect_timeout=2) as connection:
            connection.execute("SELECT 1")
        break
    except psycopg.OperationalError:
        if attempt == 29:
            raise
        time.sleep(1)
PY
"$PYTHON" -m pytest tests/integration/test_postgres_pipeline.py tests/integration/test_thermal_authority.py tests/integration/test_thermal_multiuser.py tests/integration/test_thermal_contention.py tests/integration/test_thermal_availability.py -q
