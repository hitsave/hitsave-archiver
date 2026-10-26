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

# Register or patch one dump on production Omeka (ledger status must be complete).
# Usage: register-dump-omeka.sh --slug <ready-folder-name> | --game-key <ledger game_key> [--force]
ROOT="$ARCHIVER"
CFG_DIR="${HOST_OUTPUT:-$ARCHIVER/data/output}/omeka-register"

INTAKE_HOST="${HOST_GDRIVE_INTAKE:?Set HOST_GDRIVE_INTAKE in host.env}"
PROD_CATALOG="${HITSAVE_PRIVATE_CONFIG}/omeka-catalog.yaml"
if [[ ! -f "$PROD_CATALOG" ]]; then
  echo "Missing prod omeka catalog config: $PROD_CATALOG" >&2
  exit 1
fi

SLUG=""
GAME_KEY=""
SLUG_FROM_CLI=false
FORCE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --slug) SLUG="$2"; SLUG_FROM_CLI=true; shift 2 ;;
    --game-key) GAME_KEY="$2"; shift 2 ;;
    --force) FORCE=true; shift ;;
    -h|--help)
      echo "Usage: register-dump-omeka.sh --slug SLUG | --game-key KEY [--force]"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$SLUG" && -z "$GAME_KEY" ]]; then
  echo "Provide --slug or --game-key" >&2
  exit 2
fi

READY="${HOST_GDRIVE_INTAKE:?Set HOST_GDRIVE_INTAKE}/ready"
if [[ -z "$GAME_KEY" && -n "$SLUG" ]]; then
  cfg="$READY/$SLUG/game.ingest.yaml"
  if [[ -f "$cfg" ]]; then
    GAME_KEY="$(python3 -c "import yaml; print(yaml.safe_load(open('$cfg'))['game_key'])")"
  else
    GAME_KEY="$SLUG"
  fi
fi
# When only --game-key is given, SLUG stays empty for ledger SQL (match on game_key only).
if [[ -z "$SLUG" && -n "$GAME_KEY" ]]; then
  SLUG="$GAME_KEY"
fi

# Legacy prod rows (uuid game_key) — optional title overrides.
declare -A TITLE_OVERRIDES=(
  ["famitsu-wave-dvd-2003-7gatsu-gou-tokubetsu-furoku-01a136e8-e580-7c3f-b098-f253eb0e691d"]="Famitsu Wave DVD 2003 7gatsu-gou Tokubetsu Furoku"
  ["famitsu-wave-dvd-2006-6-gatsugou-tokubetsu-furoku-01a136e4-3038-7271-bd4e-277ebe48df0c"]="Famitsu Wave DVD 2006 6-gatsugou Tokubetsu Furoku (Disc 1)"
  ["famitsu-wave-dvd-2007-8-gatsugou-tokubetsu-furoku-01a13756-77b5-7616-a3f6-d7180a42d929"]="Famitsu Wave DVD 2007 8-gatsugou Tokubetsu Furoku (Disc 2)"
  ["famitsu-wave-dvd-2007-8-gatsugou-tokubetsu-furoku-01a1375c-5bef-7f91-9375-571a57995dfc"]="Famitsu Wave DVD 2007 8-gatsugou Tokubetsu Furoku (Disc 1)"
  ["famitsu-wave-dvd-2007-9-gatsugou-tokubetsu-furoku-01a136e6-debf-73f9-a72d-c8ca413e7b12"]="Famitsu Wave DVD 2007 9-gatsugou Tokubetsu Furoku (Disc 3)"
  ["famitsu-wave-dvd-2008-12-gatsugou-tokubetsu-furoku-01a136e7-6b05-7754-a838-5cf2d5be15e5"]="Famitsu Wave DVD 2008 12-gatsugou Tokubetsu Furoku (Disc 2)"
  ["famitsu-wave-dvd-2008-1-gatsugou-tokubetsu-furoku-01a1374a-7c56-76fe-8b6a-847c296bf64b"]="Famitsu Wave DVD 2008 1-gatsugou Tokubetsu Furoku (Disc 1)"
  ["famitsu-wave-dvd-2008-1-gatsugou-tokubetsu-furoku-01a13750-029e-7b47-87c8-1c93359e20e1"]="Famitsu Wave DVD 2008 1-gatsugou Tokubetsu Furoku (Disc 2)"
  ["famitsu-wave-dvd-2008-5-gatsugou-tokubetsu-furoku-01a136e8-16bf-7e73-aef8-668c0c3c8422"]="Famitsu Wave DVD 2008 5-gatsugou Tokubetsu Furoku (Disc 3)"
  ["famitsu-wave-dvd-2008-6-gatsugou-tokubetsu-furoku-01a1373e-d7bd-75fe-9601-a84195984f9c"]="Famitsu Wave DVD 2008 6-gatsugou Tokubetsu Furoku (Disc 2)"
  ["famitsu-wave-dvd-2008-6-gatsugou-tokubetsu-furoku-01a13744-ea7c-7a91-86d7-59ed03ed62d8"]="Famitsu Wave DVD 2008 6-gatsugou Tokubetsu Furoku (Disc 1)"
)

remote_intake_archiver_compose up -d postgres >/dev/null
mkdir -p "$CFG_DIR"
OMEKA_CATALOG_MOUNT=(-v "${PROD_CATALOG}:/config/omeka-catalog.yaml:ro")

game_key_sql="${GAME_KEY//\'/\'\'}"
slug_sql="${SLUG//\'/\'\'}"
ledger_row="$(remote_intake_archiver_compose exec -T postgres psql -U hitsave -d hitsave_ledger -tAc \
  "SELECT game_key || '|' || COALESCE(status,'') || '|' || COALESCE(source_path,'') || '|' || COALESCE(omeka_item_id::text,'')
   FROM game_ingest
   WHERE ingest_kind = 'dump'
     AND (
       game_key = '${game_key_sql}'
       OR source_path LIKE '%/${slug_sql}'
       OR source_path LIKE '%/${slug_sql}/%'
     )
   ORDER BY (status = 'complete') DESC, game_key
   LIMIT 1" </dev/null 2>/dev/null \
  | tr -d '\r' || true)"

if [[ -z "$ledger_row" ]]; then
  echo "SKIP omeka $SLUG (no ledger row for slug/game_key)"
  exit 0
fi

IFS='|' read -r GAME_KEY ledger_status source_path omeka_item_id <<< "$ledger_row"
if [[ "$ledger_status" != "complete" ]]; then
  echo "SKIP omeka $GAME_KEY (ledger status=$ledger_status, need complete)"
  exit 0
fi

if [[ "$FORCE" != true && -n "$omeka_item_id" ]]; then
  echo "SKIP omeka $GAME_KEY (omeka_item_id=$omeka_item_id)"
  exit 0
fi

ready_slug="${source_path##*/}"
# Intake folder name comes from ledger source_path; --game-key is not the ready/ folder name.
if [[ "$SLUG_FROM_CLI" == true && -n "$SLUG" && "$ready_slug" != "$SLUG" ]]; then
  ready_slug="$SLUG"
fi
handoff_host="${INTAKE_HOST}/ready/${ready_slug}/hitsave-submission.yaml"
handoff_container="${source_path}/hitsave-submission.yaml"
if [[ ! -f "$handoff_host" ]]; then
  echo "SKIP omeka $GAME_KEY (no handoff at $handoff_host)" >&2
  exit 0
fi

title="$(python3 - <<'PY' "$handoff_host" "$GAME_KEY"
import sys, yaml
handoff = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
print(handoff.get("omeka_item_title") or "")
PY
)"
if [[ -z "$title" ]]; then
  title="${TITLE_OVERRIDES[$GAME_KEY]:-$GAME_KEY}"
fi

game_cfg="$CFG_DIR/${GAME_KEY}.yaml"
GAME_KEY="$GAME_KEY" SOURCE_PATH="$source_path" TITLE="$title" GAME_CFG="$game_cfg" python3 - <<'PY'
import os, yaml
from pathlib import Path
payload = {
    "ingest_kind": "dump",
    "game_key": os.environ["GAME_KEY"],
    "source_game_folder": os.environ["SOURCE_PATH"],
    "omeka_item_title": os.environ["TITLE"],
    "moby_enrich": False,
}
Path(os.environ["GAME_CFG"]).write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
PY

echo "=== prod omeka $GAME_KEY ($title) $(date -Is) ==="
remote_intake_archiver_compose run --rm -T --entrypoint python "${OMEKA_CATALOG_MOUNT[@]}" omeka-catalog \
  /app/scripts/apply_dump_catalog_omeka.py \
  "/output/omeka-register/${GAME_KEY}.yaml" \
  "$handoff_container"
echo "=== omeka done $GAME_KEY $(date -Is) ==="
