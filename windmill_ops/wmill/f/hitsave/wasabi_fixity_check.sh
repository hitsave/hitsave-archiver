#!/usr/bin/env bash
# Re-read a sample of preservation bags FROM WASABI and verify SHA-256 against their manifests
# and the ingest-time ledger inventory (scripts/wasabi-fixity-check.py). Records fixity_run rows.
# shellcheck shell=bash
# arguments of the form X="$I" are parsed as parameters X of type string
max_packages="${1:-300}"
max_gb="${2:-150}"
kind="${3:-}"
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh

args=(--max-packages "$max_packages" --max-gb "$max_gb")
[[ -n "$kind" ]] && args+=(--kind "$kind")

# Scripts mounted from the checkout so the job does not depend on the ingest-worker image contents.
archiver_compose run --rm -T --no-deps \
  -v "$ARCHIVER_HOST_PATH/scripts:/app/scripts:ro" \
  --entrypoint python ingest-worker /app/scripts/wasabi-fixity-check.py "${args[@]}"
