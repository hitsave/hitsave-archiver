#!/usr/bin/env bash
# Check the Glacier Deep Archive copy of every package without restores: each manifest file must
# be in the copy with the same size and SHA-256 metadata (scripts/glacier-copy-check.py).
# Records fixity_run rows (detail "glacier: ...") and package_copy.verified_at.
# shellcheck shell=bash
# arguments of the form X="$I" are parsed as parameters X of type string
record="${1:-true}"
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh

args=()
[[ "$record" == "false" ]] && args+=(--no-record)

# Scripts mounted from the checkout so the job does not depend on the ingest-worker image contents.
archiver_compose run --rm -T --no-deps \
  -v "$ARCHIVER_HOST_PATH/scripts:/app/scripts:ro" \
  --entrypoint python ingest-worker /app/scripts/glacier-copy-check.py "${args[@]}"
