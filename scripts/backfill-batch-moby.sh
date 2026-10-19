#!/usr/bin/env bash
# Re-run Moby enrich from batch game YAML, then PATCH Moby fields on Omeka items for that batch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BATCH="${1:?Usage: backfill-batch-moby.sh config/preservation/batch-repcopies-a-full.yml}"
BATCH_KEY="$(python3 scripts/batch_manifest.py derive-key "$BATCH")"
MANIFEST="config/preservation/generated/${BATCH_KEY}/manifest.yaml"
SOURCE_SUBPATH="$(python3 -c "
import yaml
from pathlib import Path
b = yaml.safe_load(Path('$BATCH').read_text()) or {}
print(str(b.get('source_subpath') or '').strip().strip('/'))
")"
if [[ -z "$SOURCE_SUBPATH" ]]; then
  echo "batch file must set source_subpath" >&2
  exit 1
fi
SQL_LIKE="%${SOURCE_SUBPATH}/%"

mapfile -t CONFIGS < <(python3 -c "
import yaml
from pathlib import Path
m = yaml.safe_load(Path('$MANIFEST').read_text())
for g in m['games']:
    print(g['config'])
")

docker compose build ingest-worker omeka-catalog >/dev/null
docker compose up -d postgres >/dev/null

for cfg in "${CONFIGS[@]}"; do
  rel="${cfg#config/preservation/}"
  echo "=== moby enrich: $rel ==="
  docker compose run --rm -T --entrypoint python ingest-worker \
    /app/scripts/moby_enrich_game.py "/config/preservation/$rel" </dev/null || true
done

echo "=== apply Moby metadata to Omeka (${SOURCE_SUBPATH}) ==="
mapfile -t PKG_KEYS < <(docker compose exec -T postgres psql -U hitsave -d hitsave_ledger -tAc \
  "SELECT game_key FROM game_ingest WHERE source_path LIKE '${SQL_LIKE}' AND omeka_item_id IS NOT NULL AND metadata_json IS NOT NULL ORDER BY game_key;" </dev/null)
if ((${#PKG_KEYS[@]} > 0)); then
  docker compose run --rm -T --entrypoint python omeka-catalog \
    /app/scripts/apply-moby-metadata-omeka.py "${PKG_KEYS[@]}" </dev/null
else
  echo "No Omeka rows with Moby metadata to patch for ${SOURCE_SUBPATH}."
fi

docker compose exec -T postgres psql -U hitsave -d hitsave_ledger -c \
  "SELECT moby_status, COUNT(*) FROM game_ingest WHERE source_path LIKE '${SQL_LIKE}' GROUP BY moby_status ORDER BY COUNT(*) DESC;"
