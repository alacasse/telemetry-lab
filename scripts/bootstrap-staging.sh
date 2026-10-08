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

terraform -chdir=infrastructure/bootstrap init -backend=false
terraform -chdir=infrastructure/bootstrap apply -auto-approve
