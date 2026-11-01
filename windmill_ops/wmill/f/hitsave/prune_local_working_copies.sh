#!/usr/bin/env bash
# Free /tank: delete local bags and ready/<slug> payload for packages whose Wasabi copy is
# proven equal to the ingest-time bytes (scripts/prune-local-working-copies.py).
# Dry run unless execute=true. Holds the remote-intake lock so no sweep stages meanwhile.
# shellcheck shell=bash
# arguments of the form X="$I" are parsed as parameters X of type string
execute="${1:-false}"
min_age_days="${2:-14}"
max_packages="${3:-50}"
max_gb="${4:-200}"
kind="${5:-}"
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh
RI="$ARCHIVER_ROOT/scripts/remote-intake"
# shellcheck source=../../../../scripts/remote-intake/common.sh
source "$RI/common.sh"
remote_intake_load_host_env
remote_intake_acquire_lock

args=(--min-age-days "$min_age_days" --max-packages "$max_packages" --max-gb "$max_gb")
[[ "$execute" == "true" ]] && args+=(--execute)
[[ -n "$kind" ]] && args+=(--kind "$kind")

# ingest-worker mounts ready/ read-only; add a writable mount at a separate path.
# Scripts mounted from the checkout so the job does not depend on the image contents.
remote_intake_archiver_compose run --rm -T --no-deps \
  -v "$ARCHIVER_HOST_PATH/scripts:/app/scripts:ro" \
  -v "${HOST_GDRIVE_INTAKE:?}/ready:/data/intake-ready" \
  --entrypoint python ingest-worker -u /app/scripts/prune-local-working-copies.py "${args[@]}"
