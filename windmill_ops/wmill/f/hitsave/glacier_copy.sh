#!/usr/bin/env bash
# Copy new and changed packages from Wasabi to S3 Glacier Deep Archive, checking every file's
# SHA-256 against its bag manifest on the way (scripts/glacier-copy.py, docs/glacier-copy.md).
# Records package_copy and fixity_run rows (detail "glacier-copy: ...").
# shellcheck shell=bash
# arguments of the form X="$I" are parsed as parameters X of type string
execute="${1:-true}"
max_packages="${2:-50}"
max_gb="${3:-200}"
kind="${4:-}"
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh

args=(--max-packages "$max_packages" --max-gb "$max_gb")
[[ "$execute" != "false" ]] && args+=(--execute)
[[ -n "$kind" ]] && args+=(--kind "$kind")

# Scripts mounted from the checkout so the job does not depend on the ingest-worker image contents.
archiver_compose run --rm -T --no-deps \
  -v "$ARCHIVER_HOST_PATH/scripts:/app/scripts:ro" \
  --entrypoint python ingest-worker /app/scripts/glacier-copy.py "${args[@]}"
