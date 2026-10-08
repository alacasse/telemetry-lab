#!/bin/sh
# Disposable services with dynamic loopback ports; no demo processes or volumes.
set -eu
ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"
PYTHON=${PYTHON:-.run/demo-python312/bin/python}
"$PYTHON" -c 'import pytest, psycopg, boto3, alembic, sqlalchemy'
docker info >/dev/null
PG=$(docker run -d --rm -e POSTGRES_DB=thermal_migration -e POSTGRES_USER=telemetry_lab -e POSTGRES_PASSWORD=local-test -p 127.0.0.1::5432 postgres:16-alpine)
SQS=''
trap 'docker stop "$PG" >/dev/null; if [ -n "$SQS" ]; then docker stop "$SQS" >/dev/null; fi' EXIT HUP INT TERM
SQS=$(docker run -d --rm -e SERVICES=sqs -e SQS_ENDPOINT_STRATEGY=path -e AWS_DEFAULT_REGION=us-east-1 -p 127.0.0.1::4566 localstack/localstack:3.4)
ATTEMPT=0
until docker exec "$PG" pg_isready -U telemetry_lab -d thermal_migration >/dev/null 2>&1 && docker exec "$SQS" curl -fsS http://localhost:4566/_localstack/health >/dev/null 2>&1; do
  ATTEMPT=$((ATTEMPT + 1))
  [ "$ATTEMPT" -lt 60 ] || exit 1
  sleep 1
done
PG_PORT=$(docker port "$PG" 5432/tcp | sed 's/.*://')
SQS_PORT=$(docker port "$SQS" 4566/tcp | sed 's/.*://')
export TELEMETRY_LAB_MIGRATION_POSTGRES_URL="postgresql+psycopg://telemetry_lab:local-test@127.0.0.1:$PG_PORT/thermal_migration"
export TELEMETRY_LAB_MIGRATION_SQS_ENDPOINT="http://127.0.0.1:$SQS_PORT"
export TELEMETRY_LAB_MIGRATION_PRIVATE_SERVICES=1
export TELEMETRY_LAB_MIGRATION_CONTAINERS="{\"postgres\":\"$PG\",\"localstack\":\"$SQS\"}"
"$PYTHON" -m pytest tests/integration/test_thermal_command_migration.py -q
