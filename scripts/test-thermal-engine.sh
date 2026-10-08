#!/bin/sh
# All application and service traffic stays on this disposable Compose network.
set -eu
ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"
export COMPOSE_PROJECT_NAME="thermal-engine-test-$(date +%s)-$$"
export COMPOSE_FILE="$ROOT_DIR/compose.thermal-engine.yml"
cleanup() { docker compose --profile validation down --volumes --remove-orphans >/dev/null; }
trap cleanup EXIT HUP INT TERM
docker info >/dev/null
docker compose --profile validation build
"${PYTHON:-python3}" scripts/thermal-engine-validation.py
