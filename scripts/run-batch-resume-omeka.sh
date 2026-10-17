#!/usr/bin/env bash
# Resume batch ingest + Omeka catalog register (omeka-catalog skips rows already in Omeka).
#   --ingest-only   Run ingest-worker only (no Omeka catalog register).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

INGEST_ONLY=0
if [[ "${1:-}" == "--ingest-only" ]]; then
  INGEST_ONLY=1
  shift
fi

BATCH="${1:-config/preservation/batch.yml}"
BATCH_KEY="$(python3 scripts/batch_manifest.py derive-key "$BATCH")"
MANIFEST="config/preservation/generated/${BATCH_KEY}/manifest.yaml"
export HITSAVE_BATCH_KEY="$BATCH_KEY"

mapfile -t CONFIGS < <(python3 -c "
import yaml
from pathlib import Path
m = yaml.safe_load(Path('$MANIFEST').read_text())
for g in m['games']:
    print(g['config'])
")

docker compose build ingest-worker omeka-catalog >/dev/null

ledger_status() {
  local cfg="$1"
  local rel="${cfg#config/preservation/}"
  docker compose run --rm --entrypoint python ingest-worker \
    /app/scripts/batch_ledger.py status "/config/preservation/$rel" 2>/dev/null | tr -d '[:space:]' || true
}

game_label() {
  local cfg="$1"
  python3 -c "
import yaml
from pathlib import Path
d = yaml.safe_load(Path('$cfg').read_text()) or {}
print(d.get('omeka_item_title') or d.get('source_subpath') or Path('$cfg').stem)
"
}

for cfg in "${CONFIGS[@]}"; do
  label="$(game_label "$cfg")"
  status="$(ledger_status "$cfg")"
  if [[ "$status" != "complete" && "$status" != "quarantine" ]]; then
    echo "=== ingest: $label ==="
    if ! docker compose run --rm -T -e HITSAVE_BATCH_KEY="$BATCH_KEY" ingest-worker \
      "/config/preservation/${cfg#config/preservation/}" </dev/null; then
      echo "=== ingest failed (continuing batch): $label ===" >&2
    fi
    status="$(ledger_status "$cfg")"
  elif [[ "$status" == "quarantine" ]]; then
    echo "=== ingest skipped (quarantine): $label ==="
  fi
  if [[ "$INGEST_ONLY" -eq 0 ]]; then
    if [[ "$status" == "complete" ]]; then
      rel="${cfg#config/preservation/}"
      needs_moby="$(docker compose run --rm --entrypoint python ingest-worker \
        /app/scripts/batch_ledger.py needs_moby "/config/preservation/$rel" 2>/dev/null | tr -d '[:space:]' || echo 0)"
      if [[ "$needs_moby" == "1" ]]; then
        echo "=== moby enrich (ledger backfill): $label ==="
        docker compose run --rm -T -e HITSAVE_BATCH_KEY="$BATCH_KEY" --entrypoint python ingest-worker \
          /app/scripts/moby_enrich_game.py "/config/preservation/$rel" </dev/null || true
      fi
      pkg_key="$(docker compose run --rm --entrypoint python ingest-worker \
        /app/scripts/batch_ledger.py game_key "/config/preservation/$rel" 2>/dev/null | tr -d '[:space:]' || true)"
      echo "=== omeka catalog register: ${pkg_key:-$label} ==="
      if ! docker compose run --rm -T omeka-catalog "/config/preservation/${cfg#config/preservation/}" </dev/null; then
        echo "=== omeka catalog register failed (continuing batch): $label ===" >&2
      fi
    else
      echo "=== omeka catalog register skipped: $label (ledger status=${status:-none}) ==="
    fi
  fi
done

echo "Resume complete."
