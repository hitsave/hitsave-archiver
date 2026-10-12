#!/usr/bin/env bash
# Re-run omeka-uploader (skip path) to apply staff AIP s3 URIs on existing items.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BATCH_KEY="${1:-repcopies-b-full}"
GEN="config/preservation/generated/${BATCH_KEY}"
PREFIX="${BATCH_KEY}"

mapfile -t KEYS < <(
  docker compose exec -T postgres psql -U hitsave -d hitsave_ledger -tAc \
    "SELECT game_key FROM game_ingest
     WHERE game_key LIKE '${PREFIX}%'
       AND status = 'complete'
       AND omeka_item_id IS NOT NULL
       AND aip_wasabi_prefix IS NOT NULL
     ORDER BY game_key;"
)

echo "Applying staff AIP URI for ${#KEYS[@]} ledger rows (${PREFIX}*)"
docker compose build omeka-uploader >/dev/null

for key in "${KEYS[@]}"; do
  key="$(echo "$key" | tr -d ' ')"
  cfg="${GEN}/${key}.yaml"
  if [[ ! -f "$cfg" ]]; then
    echo "skip $key: missing $cfg" >&2
    continue
  fi
  echo "=== staff AIP: $key ==="
  if ! docker compose run --rm -T omeka-uploader "$key" "/config/preservation/${cfg#config/preservation/}" </dev/null; then
    echo "=== failed (continuing): $key ===" >&2
  fi
done

echo "Staff AIP batch apply complete."
