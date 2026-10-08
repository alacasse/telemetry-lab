#!/bin/sh
# Local packaging only. Transient installation writes use tmpfs; final bundles
# remain durable in the explicit new output directory. This is an experimental
# contention mitigation, not proof of a fix; keep source unchanged during A/B runs.
set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: sh scripts/build-lambda-bundles-local.sh /absolute/new/output/directory" >&2
    exit 2
fi
case "$1" in
    /*) ;;
    *) echo "Output directory must be absolute" >&2; exit 2 ;;
esac
case "$1" in
    *:*) echo "Output directory cannot contain ':' (Docker mount separator)" >&2; exit 2 ;;
esac
if [ -e "$1" ] || [ -L "$1" ]; then
    echo "Refusing existing output path: $1" >&2
    exit 2
fi
ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
SAM_IMAGE=${SAM_BUILD_IMAGE:-public.ecr.aws/sam/build-python3.12:latest}
# Require an already available image; never pull a build image implicitly.
docker image inspect "$SAM_IMAGE" >/dev/null
SOURCE_VERSION=$(git -C "$ROOT_DIR" rev-parse HEAD)
if [ -n "$(git -C "$ROOT_DIR" status --porcelain)" ]; then
    SOURCE_VERSION="${SOURCE_VERSION}-dirty"
fi
mkdir -p -- "$(dirname "$1")"
# mkdir is deliberately exclusive: a concurrent creation cannot be overwritten.
mkdir -- "$1"
OUTPUT_DIR=$(CDPATH= cd -- "$1" && pwd)
CONTAINER_NAME="telemetry-lambda-local-$$-$(date +%s)"
cleanup() {
    # Remove only this invocation's uniquely named container on interruption.
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || :
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'Source: %s\nOutput: %s\nImage: %s\n' "$SOURCE_VERSION" "$OUTPUT_DIR" "$SAM_IMAGE"
docker run --rm --init --name "$CONTAINER_NAME" --pull never --platform linux/amd64 \
    --tmpfs /tmp:rw,exec,size=2g \
    -v "$ROOT_DIR:/src:ro" -v "$OUTPUT_DIR:/out" -w /src \
    -e SOURCE_VERSION="$SOURCE_VERSION" -e UV_VERSION=0.11.6 \
    -e UV_CACHE_DIR=/tmp/uv-cache -e PIP_CACHE_DIR=/tmp/pip-cache \
    -e UV_PROJECT_ENVIRONMENT=/tmp/lambda-build-venv \
    -e GIT_CONFIG_GLOBAL=/tmp/gitconfig \
    --entrypoint /bin/bash "$SAM_IMAGE" -euo pipefail -c '
        git config --global --add safe.directory /src
        python3.12 -m pip install --target /tmp/uv-bootstrap "uv==$UV_VERSION"
        export PYTHONPATH=/tmp/uv-bootstrap
        export PATH="/tmp/uv-bootstrap/bin:$PATH"
        uv sync --frozen --group dev --python 3.12
        uv run --frozen python scripts/package-lambdas.py \
            --runtime python3.12 --architecture x86_64 --output-dir /out
    '
