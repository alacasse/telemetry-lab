#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required but not installed"
  exit 1
fi

cp -n .env.example .env || true
uv sync --group dev
docker compose up -d postgres localstack
uv run alembic upgrade head

echo "Local bootstrap complete. Start services from the README."
