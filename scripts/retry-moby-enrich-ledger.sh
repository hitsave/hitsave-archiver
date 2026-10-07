#!/usr/bin/env bash
# Re-run moby_enrich_game.py for ledger rows (default: incomplete, ambiguous, no_match).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
STATUSES="${*:-incomplete ambiguous no_match}"
STATUS_SQL=$(printf "'%s'," $STATUSES | sed "s/,$//")

docker compose up -d postgres >/dev/null

mapfile -t KEYS < <(
  docker compose exec -T postgres psql -U hitsave -d hitsave_ledger -tAc \
    "SELECT game_key FROM game_ingest WHERE moby_status IN ($STATUS_SQL) ORDER BY game_key;" </dev/null
)

echo "Retry Moby enrich for ${#KEYS[@]} games (statuses: $STATUSES)"
for key in "${KEYS[@]}"; do
  [[ -z "$key" ]] && continue
  matches=("$ROOT"/config/preservation/generated/*/"${key}.yaml")
  cfg=""
  if [[ -f "${matches[0]:-}" ]]; then
    cfg="/config/preservation/generated/$(basename "$(dirname "${matches[0]}")")/${key}.yaml"
  elif [[ "$key" == "pilot-wog1" ]]; then
    cfg="/config/preservation/pilot-game.yaml"
  else
    echo "SKIP $key (no generated game yaml)"
    continue
  fi
  echo "=== moby enrich: $key ==="
  docker compose run --rm -T --entrypoint python ingest-worker \
    /app/scripts/moby_enrich_game.py "$cfg" </dev/null || true
done

echo "Applying Moby fields to Omeka…"
docker compose run --rm -T --entrypoint python omeka-uploader \
  /app/scripts/apply-moby-metadata-omeka.py --all </dev/null

docker compose exec -T postgres psql -U hitsave -d hitsave_ledger -c \
  "SELECT moby_status, COUNT(*) FROM game_ingest GROUP BY moby_status ORDER BY COUNT(*) DESC;" </dev/null
