#!/usr/bin/env bash
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env
ARCHIVER="$(remote_intake_archiver_root)"
GDRIVE="$(remote_intake_gdrive_root)"
BUNDLE="$(remote_intake_bundle_root)"
GEN="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-$ARCHIVER/.generated}"
mkdir -p "$GEN"

# Reconcile: register complete dumps missing omeka_item_id on production Omeka.


LOG="$GEN/register-prod-dumps-omeka.log"
REGISTER_ONE="$RI/register-dump-omeka.sh"


cd "$ARCHIVER"

exec > >(tee -a "$LOG") 2>&1
echo "=== prod Omeka dump reconcile (missing omeka_item_id) $(date -Is) ==="

docker compose up -d postgres >/dev/null
mapfile -t keys < <(
  docker compose exec -T postgres psql -U hitsave -d hitsave_ledger -tAc \
    "SELECT game_key FROM game_ingest
     WHERE ingest_kind = 'dump' AND status = 'complete' AND omeka_item_id IS NULL
     ORDER BY game_key;" </dev/null
)

if [[ ${#keys[@]} -eq 0 ]]; then
  echo "Nothing to reconcile."
else
  echo "Rows to register: ${#keys[@]}"
  for game_key in "${keys[@]}"; do
    [[ -z "$game_key" ]] && continue
    if "$REGISTER_ONE" --game-key "$game_key"; then
      :
    else
      echo "WARN: omeka register failed for $game_key $(date -Is)"
    fi
  done
fi

echo "=== prod Omeka dump reconcile finished $(date -Is) ==="
