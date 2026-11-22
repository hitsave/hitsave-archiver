#!/usr/bin/env bash
# Post newly published archive items to the Discord changelog channel (scripts/discord_digest.py).
# Dry run when execute=false. See docs/discord-digest.md.
# shellcheck shell=bash
# arguments of the form X="$I" are parsed as parameters X of type string
execute="${1:-true}"
max_items="${2:-40}"
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh

args=(--max-items "$max_items")
[[ "$execute" != "false" ]] && args+=(--execute)

# Scripts mounted from the checkout so the job does not depend on the omeka-catalog image contents.
archiver_compose run --rm -T --no-deps \
  -v "$ARCHIVER_HOST_PATH/scripts:/app/scripts:ro" \
  --entrypoint python omeka-catalog /app/scripts/discord_digest.py "${args[@]}"
