#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"

TERRAFORM_VERSION=${TERRAFORM_VERSION:-1.8.5}
if ! command -v terraform >/dev/null 2>&1; then
  uv run python scripts/install-terraform.py --version "$TERRAFORM_VERSION" --install-dir .local/bin >/dev/null
  PATH="$ROOT_DIR/.local/bin:$PATH"
  export PATH
fi

uv run python scripts/require-env.py \
  --var AWS_LAMBDA_ARTIFACT_BUCKET \
  --var TF_STATE_BUCKET \
  --var TF_STATE_LOCK_TABLE

REVISION=${STAGING_ARTIFACT_REVISION:-${LAMBDA_ARTIFACT_REVISION:-${CI_COMMIT_SHA:-local}}}
SKIP_FINAL_SNAPSHOT=true
if [ "${PRESERVE_FINAL_SNAPSHOT:-1}" != "0" ]; then
  SKIP_FINAL_SNAPSHOT=false
fi

terraform -chdir=infrastructure/staging init \
  -input=false \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="dynamodb_table=$TF_STATE_LOCK_TABLE" \
  -backend-config="key=${TF_STATE_KEY:-telemetry-lab/staging/terraform.tfstate}" \
  -backend-config="region=${AWS_REGION:-ca-central-1}"

terraform -chdir=infrastructure/staging destroy \
  -auto-approve \
  -input=false \
  -var="lambda_artifact_bucket=$AWS_LAMBDA_ARTIFACT_BUCKET" \
  -var="lambda_artifact_revision=$REVISION" \
  -var="skip_final_snapshot=$SKIP_FINAL_SNAPSHOT"
