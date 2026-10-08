#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  printf 'usage: sh scripts/rollback-staging.sh <artifact-sha>\n' >&2
  exit 1
fi

export STAGING_ARTIFACT_REVISION="$1"
exec sh "$(dirname "$0")/deploy-staging.sh"
