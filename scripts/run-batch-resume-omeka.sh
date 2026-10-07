#!/usr/bin/env bash
# Resume batch ingest + Omeka upload (uploader skips rows that verify in Omeka).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BATCH="${1:-config/preservation/batch-repcopies-t.yaml}"
MANIFEST="config/preservation/generated/$(python3 -c "import yaml; from pathlib import Path; print(yaml.safe_load(Path('$BATCH').read_text())['batch_key'])")/manifest.yaml"

mapfile -t CONFIGS < <(python3 -c "
import yaml
from pathlib import Path
m = yaml.safe_load(Path('$MANIFEST').read_text())
for g in m['games']:
    print(g['config'])
")

docker compose build ingest-worker omeka-uploader >/dev/null

for cfg in "${CONFIGS[@]}"; do
  key="$(python3 -c "import yaml; print(yaml.safe_load(open('$cfg'))['game_key'])")"
  status="$(docker compose exec -T postgres psql -U hitsave -d hitsave_ledger -tAc \
    "SELECT status FROM game_ingest WHERE game_key='${key}' LIMIT 1" 2>/dev/null | tr -d ' ' || true)"
  if [[ "$status" != "complete" ]]; then
    echo "=== ingest: $key ==="
    docker compose run --rm ingest-worker "/config/preservation/${cfg#config/preservation/}"
  fi
  echo "=== omeka upload: $key ==="
  docker compose run --rm omeka-uploader "$key" "/config/preservation/${cfg#config/preservation/}"
done

echo "Resume complete."
