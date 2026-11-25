#!/usr/bin/env bash
# Retry Omeka registrations that failed after the package was bagged
# (scripts/catalog_registration.py). Dry run when execute=false. See docs/catalog-registration-retry.md.
# shellcheck shell=bash
# arguments of the form X="$I" are parsed as parameters X of type string
execute="${1:-true}"
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh

args=()
[[ "$execute" != "false" ]] && args+=(--execute)

# Scripts mounted from the checkout so the job does not depend on the omeka-catalog image contents.
archiver_compose run --rm -T --no-deps \
  -v "$ARCHIVER_HOST_PATH/scripts:/app/scripts:ro" \
  --entrypoint python omeka-catalog /app/scripts/catalog_registration.py "${args[@]}"
