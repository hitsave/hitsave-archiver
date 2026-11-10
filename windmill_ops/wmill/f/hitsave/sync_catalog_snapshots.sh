#!/usr/bin/env bash
# Add a dated copy of each package's catalog record to its bag when the Omeka record changed
# (scripts/sync-catalog-snapshots.py). Unchanged records are only read.
# shellcheck shell=bash
# arguments of the form X="$I" are parsed as parameters X of type string
execute="${1:-true}"
max_writes="${2:-0}"
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh

args=(--max-writes "$max_writes")
[[ "$execute" != "false" ]] && args+=(--execute)

# Scripts mounted from the checkout so the job does not depend on the omeka-catalog image contents.
archiver_compose run --rm -T --no-deps \
  -v "$ARCHIVER_HOST_PATH/scripts:/app/scripts:ro" \
  --entrypoint python omeka-catalog /app/scripts/sync-catalog-snapshots.py "${args[@]}"
