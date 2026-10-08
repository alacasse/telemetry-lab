#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"

uv run python scripts/require-env.py --var TERRAFORM_OUTPUT_API_ENDPOINT

if [ -z "${STAGING_AUTH_TOKEN:-}" ] && [ -n "${TERRAFORM_OUTPUT_STAGING_AUTH_SECRET_ARN:-}" ]; then
  STAGING_AUTH_TOKEN=$(uv run python - <<'PY'
import boto3
import os

client = boto3.client("secretsmanager", region_name=os.getenv("AWS_REGION", "ca-central-1"))
response = client.get_secret_value(SecretId=os.environ["TERRAFORM_OUTPUT_STAGING_AUTH_SECRET_ARN"])
print(response["SecretString"])
PY
)
  export STAGING_AUTH_TOKEN
fi

export INGESTION_BASE_URL="$TERRAFORM_OUTPUT_API_ENDPOINT"
export QUERY_BASE_URL="$TERRAFORM_OUTPUT_API_ENDPOINT"
uv run python scripts/run-smoke-tests.py
