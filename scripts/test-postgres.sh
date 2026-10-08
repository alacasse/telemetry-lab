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
"$PYTHON" -m pytest tests/integration/test_postgres_pipeline.py -q
