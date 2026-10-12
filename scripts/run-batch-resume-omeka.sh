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
MANIFEST="config/preservation/generated/$(python3 -c "import yaml; from pathlib import Path; print(yaml.safe_load(Path('$BATCH').read_text())['batch_key'])")/manifest.yaml"

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

for cfg in "${CONFIGS[@]}"; do
  key="$(python3 -c "import yaml; print(yaml.safe_load(open('$cfg'))['game_key'])")"
  status="$(ledger_status "$cfg")"
  if [[ "$status" != "complete" && "$status" != "quarantine" ]]; then
    echo "=== ingest: $key ==="
    if ! docker compose run --rm -T ingest-worker "/config/preservation/${cfg#config/preservation/}" </dev/null; then
      echo "=== ingest failed (continuing batch): $key ===" >&2
    fi
    status="$(ledger_status "$cfg")"
  elif [[ "$status" == "quarantine" ]]; then
    echo "=== ingest skipped (quarantine): $key ==="
  fi
  if [[ "$INGEST_ONLY" -eq 0 ]]; then
    if [[ "$status" == "complete" ]]; then
      rel="${cfg#config/preservation/}"
      needs_moby="$(docker compose run --rm --entrypoint python ingest-worker \
        /app/scripts/batch_ledger.py needs_moby "/config/preservation/$rel" 2>/dev/null | tr -d '[:space:]' || echo 0)"
      if [[ "$needs_moby" == "1" ]]; then
        echo "=== moby enrich (ledger backfill): $key ==="
        docker compose run --rm -T --entrypoint python ingest-worker \
          /app/scripts/moby_enrich_game.py "/config/preservation/$rel" </dev/null || true
      fi
      pkg_key="$(docker compose run --rm --entrypoint python ingest-worker \
        /app/scripts/batch_ledger.py game_key "/config/preservation/$rel" 2>/dev/null | tr -d '[:space:]' || true)"
      echo "=== omeka catalog register: ${pkg_key:-$key} ==="
      if ! docker compose run --rm -T omeka-catalog "/config/preservation/${cfg#config/preservation/}" </dev/null; then
        echo "=== omeka catalog register failed (continuing batch): $key ===" >&2
      fi
    else
      echo "=== omeka catalog register skipped: $key (ledger status=${status:-none}) ==="
    fi
  fi
done

echo "Resume complete."
