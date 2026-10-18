#!/usr/bin/env bash
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env
ARCHIVER="$(remote_intake_archiver_root)"
GDRIVE="$(remote_intake_gdrive_root)"
GEN="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-$ARCHIVER/.generated}"
mkdir -p "$GEN"
LOG="$GEN/move-gdrive-redump-to-processed.log"
POLL_JSON="$GEN/gdrive-redump-poll.json"
EXECUTE=false
CREATE=false
for arg in "$@"; do
  case "$arg" in
    --execute) EXECUTE=true ;;
    --create-processed-folder) CREATE=true ;;
  esac
done

exec > >(tee -a "$LOG") 2>&1
echo "=== gdrive redump → processed $(date -Is) execute=$EXECUTE ==="

cd "$GDRIVE"
if [[ "$EXECUTE" == true ]]; then
  docker compose run --rm -T --user 1000:1000 gdrive-intake poll --once -v --config-root /config \
    > "$POLL_JSON"
else
  docker compose run --rm -T --user 1000:1000 gdrive-intake poll --once -v --config-root /config \
    > "$POLL_JSON" || cp -f "$ARCHIVER/../gdrive-redump-poll.json" "$POLL_JSON" 2>/dev/null || true
fi

MOVE_PY="/repo/archiver/scripts/remote-intake/move_gdrive_redump_to_processed.py"
ARGS=(
  --config-root /config
  --ingest-config /config/preservation/ingest.yaml
  --poll-json "/poll/gdrive-redump-poll.json"
)
if [[ "$EXECUTE" == true ]]; then
  ARGS+=(--execute)
  [[ "$CREATE" == true ]] && ARGS+=(--create-processed-folder)
fi

cd "$GDRIVE"
docker compose run --rm --no-deps --user 1000:1000 \
  -v "$ARCHIVER:/repo/archiver:ro" \
  -v "${HITSAVE_PRIVATE_CONFIG}/preservation/ingest.yaml:/config/preservation/ingest.yaml:ro" \
  -v "${HITSAVE_PRIVATE_CONFIG}/preservation/database.yaml:/config/preservation/database.yaml:ro" \
  -v "${HITSAVE_PRIVATE_CONFIG}/secrets:/config/secrets:ro" \
  -v "$POLL_JSON:/poll/gdrive-redump-poll.json:ro" \
  --entrypoint python \
  gdrive-intake \
  "$MOVE_PY" \
  "${ARGS[@]}"

echo "=== finished $(date -Is) ==="
