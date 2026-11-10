#!/usr/bin/env bash
# Republish the public "Damaged files" page on the archive from the ledger
# (scripts/publish-damaged-files-page.py). Lists damaged files of public items only.
# shellcheck shell=bash
# arguments of the form X="$I" are parsed as parameters X of type string
execute="${1:-true}"
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh

args=(--out /tmp/damaged-files)
[[ "$execute" != "false" ]] && args+=(--execute)

# Scripts mounted from the checkout so the job does not depend on the omeka-catalog image contents.
# The page is rebuilt from the ledger every run, so the old-page backup stays in the throwaway container.
archiver_compose run --rm -T --no-deps \
  -v "$ARCHIVER_HOST_PATH/scripts:/app/scripts:ro" \
  --entrypoint python omeka-catalog /app/scripts/publish-damaged-files-page.py "${args[@]}"
