#!/usr/bin/env bash
# Re-run Moby enrich from batch game YAML (BagIt package_id ledger rows), then patch Omeka.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BATCH="${1:?Usage: backfill-batch-moby.sh config/preservation/batch-repcopies-f-full.yml}"
BATCH_KEY="$(python3 scripts/batch_manifest.py derive-key "$BATCH")"
MANIFEST="config/preservation/generated/${BATCH_KEY}/manifest.yaml"

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

echo "=== apply Moby metadata to Omeka (F batch items with Moby metadata) ==="
mapfile -t PKG_KEYS < <(docker compose exec -T postgres psql -U hitsave -d hitsave_ledger -tAc \
  "SELECT game_key FROM game_ingest WHERE source_path LIKE '%Representation_Copies/F/%' AND omeka_item_id IS NOT NULL AND metadata_json IS NOT NULL ORDER BY game_key;" </dev/null)
if ((${#PKG_KEYS[@]} > 0)); then
  docker compose run --rm -T --entrypoint python omeka-catalog \
    /app/scripts/apply-moby-metadata-omeka.py "${PKG_KEYS[@]}" </dev/null
else
  echo "No F-batch Omeka rows with Moby metadata to patch."
fi

docker compose exec -T postgres psql -U hitsave -d hitsave_ledger -c \
  "SELECT moby_status, COUNT(*) FROM game_ingest WHERE source_path LIKE '%Representation_Copies/F/%' GROUP BY moby_status ORDER BY COUNT(*) DESC;"
