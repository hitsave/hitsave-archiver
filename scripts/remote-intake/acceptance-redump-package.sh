#!/usr/bin/env bash
# Phase 1 acceptance spot-check for one Redump package (ledger + preservation Wasabi bag).
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env
ARCHIVER="$(remote_intake_archiver_root)"
SLUG=""
GAME_KEY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --slug) SLUG="$2"; shift 2 ;;
    --game-key) GAME_KEY="$2"; shift 2 ;;
    *) echo "usage: $0 --slug ready-slug | --game-key ledger-game_key" >&2; exit 2 ;;
  esac
done
if [[ -z "$SLUG" && -z "$GAME_KEY" ]]; then
  echo "usage: $0 --slug ready-slug | --game-key ledger-game_key" >&2
  exit 2
fi

cd "$ARCHIVER"
docker compose up -d postgres >/dev/null

if [[ -n "$SLUG" ]]; then
  slug_sql="${SLUG//\'/\'\'}"
  WHERE="(source_path LIKE '%/${slug_sql}' OR source_path LIKE '%/${slug_sql}/%')"
else
  gk_sql="${GAME_KEY//\'/\'\'}"
  WHERE="game_key = '${gk_sql}'"
fi

echo "=== ledger (ingest_kind=dump) ==="
docker compose exec -T postgres psql -U hitsave -d hitsave_ledger -c \
  "SELECT game_key, status, package_storage_prefix, omeka_item_id
   FROM game_ingest WHERE ingest_kind = 'dump' AND ${WHERE}
   ORDER BY updated_at DESC LIMIT 3;"

echo "=== preservation Wasabi (validate_dump_wasabi_packages.py) ==="
export HITSAVE_PRIVATE_CONFIG="${HITSAVE_PRIVATE_CONFIG:-$(cd "$ARCHIVER/.." && pwd)/hitsave-archiver-config}"
docker compose run --rm --no-deps \
  -v "$HITSAVE_PRIVATE_CONFIG/secrets:/config/secrets:ro" \
  -v "$HITSAVE_PRIVATE_CONFIG/preservation/ingest.yaml:/config/preservation/ingest.yaml:ro" \
  --entrypoint python ingest-worker /app/scripts/validate_dump_wasabi_packages.py --report 2>&1 \
  | tail -5
