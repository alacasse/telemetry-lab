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
export STAGING_ARTIFACT_REVISION="$REVISION"
mkdir -p dist/staging dist/terraform
printf 'STAGING_ARTIFACT_REVISION=%s\n' "$STAGING_ARTIFACT_REVISION" > dist/staging/deploy.env

if [ ! -f dist/lambdas/manifest.json ]; then
  uv run python scripts/package-lambdas.py
fi

uv run python scripts/upload-lambda-artifacts.py \
  --manifest dist/lambdas/manifest.json \
  --bucket "$AWS_LAMBDA_ARTIFACT_BUCKET"

terraform -chdir=infrastructure/staging init \
  -input=false \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="dynamodb_table=$TF_STATE_LOCK_TABLE" \
  -backend-config="key=${TF_STATE_KEY:-telemetry-lab/staging/terraform.tfstate}" \
  -backend-config="region=${AWS_REGION:-ca-central-1}"

terraform -chdir=infrastructure/staging apply \
  -auto-approve \
  -input=false \
  -var="lambda_artifact_bucket=$AWS_LAMBDA_ARTIFACT_BUCKET" \
  -var="lambda_artifact_revision=$STAGING_ARTIFACT_REVISION"

terraform -chdir=infrastructure/staging output -json > dist/terraform/outputs.json
uv run python scripts/export-terraform-outputs-env.py \
  --input dist/terraform/outputs.json \
  --output dist/terraform/outputs.env \
  --required-output api_endpoint \
  --required-output migration_function_name \
  --required-output staging_auth_secret_arn
cat dist/staging/deploy.env >> dist/terraform/outputs.env
