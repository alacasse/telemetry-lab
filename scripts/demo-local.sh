#!/bin/sh
# Isolated Python environment; never replaces the project's pre-existing .venv.
set -eu
ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"
export UV_PROJECT_ENVIRONMENT="$ROOT_DIR/.run/demo-python312"
exec uv run --frozen --python 3.12 scripts/demo-local.py "$@"
