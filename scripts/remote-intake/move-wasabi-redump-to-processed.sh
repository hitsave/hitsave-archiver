#!/usr/bin/env bash
# MOVE ledger-complete Wasabi incoming Redump SIP trees to processed/{source}/{folder}/.
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env
ARCHIVER="$(remote_intake_archiver_root)"
GDRIVE="$(remote_intake_gdrive_root)"
GEN="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-$ARCHIVER/.generated}"
mkdir -p "$GEN"
POLL_JSON="$GEN/wasabi-move-to-processed-poll.json"
INTAKE_CFG="${HITSAVE_PRIVATE_CONFIG}/preservation/wasabi-redump-intake.yaml"
LOG="$GEN/move-wasabi-redump-to-processed.log"
EXECUTE=false
if [[ "${1:-}" == "--execute" ]]; then
  EXECUTE=true
fi

exec > >(tee -a "$LOG") 2>&1
echo "=== Wasabi incoming → processed/ started $(date -Is) execute=$EXECUTE ==="

cd "$GDRIVE"
docker compose run --rm -T --user 1000:1000 intake poll-wasabi --once --config-root /config \
  > "$POLL_JSON"

cd "$ARCHIVER"
docker compose up -d postgres >/dev/null

ARGS=(--intake-config "/tmp/wasabi-redump-intake.yaml" --poll-json "/poll/wasabi-poll.json")
if [[ "$EXECUTE" == true ]]; then
  ARGS+=(--execute --overwrite)
fi

docker compose run --rm --no-deps \
  -v "$PWD/scripts:/app/scripts:ro" \
  -v "$INTAKE_CFG:/tmp/wasabi-redump-intake.yaml:ro" \
  -v "$POLL_JSON:/poll/wasabi-poll.json:ro" \
  --entrypoint python ingest-worker \
  /app/scripts/remote-intake/move_wasabi_intake_to_processed.py "${ARGS[@]}"

echo "=== finished $(date -Is) ==="
