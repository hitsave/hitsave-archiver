#!/usr/bin/env bash
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env
remote_intake_acquire_lock
ARCHIVER="$(remote_intake_archiver_root)"
GDRIVE="$(remote_intake_gdrive_root)"
BUNDLE="$(remote_intake_bundle_root)"
GEN="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-$ARCHIVER/.generated}"
mkdir -p "$GEN"

# For each Redump package on Wasabi incoming: stage → ingest → prod Omeka when ledger complete.
BUNDLE="$(remote_intake_bundle_root)"


LOG="$GEN/run-wasabi-redump-per-folder.log"
POLL_JSON="$GEN/wasabi-redump-poll.json"




READY="${HOST_INTAKE}/ready"

exec > >(tee -a "$LOG") 2>&1
echo "=== Wasabi Redump per-folder pipeline started $(date -Is) ==="

remote_intake_archiver_compose up -d postgres clamav >/dev/null

mapfile -t pairs < <("$RI/wasabi-redump-poll-packages.sh")
echo "packages on Wasabi: ${#pairs[@]}"

ledger_status_for_slug() {
  local slug="$1"
  local slug_sql="${slug//\'/\'\'}"
  remote_intake_archiver_compose exec -T postgres psql -U hitsave -d hitsave_ledger -tAc \
    "SELECT COALESCE(status,'')
     FROM game_ingest
     WHERE ingest_kind = 'dump'
       AND (source_path LIKE '%/${slug_sql}' OR source_path LIKE '%/${slug_sql}/%')
     ORDER BY (status = 'complete') DESC, game_key
     LIMIT 1" </dev/null 2>/dev/null \
    | tr -d '[:space:]' || true
}

register_omeka() {
  local slug="$1"
  if "$RI/register-dump-omeka.sh" --slug "$slug"; then
    echo "--- omeka ok $slug ---"
  else
    echo "WARN: omeka register failed for $slug $(date -Is)"
  fi
}

for pair in "${pairs[@]}"; do
  IFS=$'\t' read -r source_prefix pkg_name slug <<< "$pair"
  if [[ "$source_prefix" == "." ]]; then
    source_prefix=""
  fi
  if [[ -z "$slug" || -z "$pkg_name" ]]; then
    echo "WARN: skip malformed poll line: $pair"
    continue
  fi
  if ! remote_intake_ready_source_guard "$READY" "$slug" \
    --wasabi-prefix "${source_prefix:+$source_prefix/}$pkg_name"; then
    continue
  fi
  cfg="$READY/$slug/game.ingest.yaml"

  if [[ -f "$cfg" ]]; then
    game_key="$(python3 -c "import yaml; print(yaml.safe_load(open('$cfg'))['game_key'])")"
  else
    game_key="$slug"
  fi

  status="$(ledger_status_for_slug "$slug")"
  if [[ "$status" == "complete" ]]; then
    echo "SKIP ingest (ledger complete): $slug"
    register_omeka "$slug"
    continue
  fi

  echo ""
  echo "=== package $slug ($game_key) ledger=${status:-none} $(date -Is) ==="

  if [[ ! -f "$cfg" ]]; then
    echo "--- stage from Wasabi ${source_prefix:+$source_prefix/}$pkg_name ---"
    if remote_intake_gdrive_compose run --rm -T --user 1000:1000 intake process-wasabi \
      --source-prefix "$source_prefix" \
      --package-folder-name "$pkg_name" \
      --config-root /config; then
      echo "--- staged $slug ---"
    else
      echo "--- FAILED stage $slug (skip ingest) ---"
      continue
    fi
    cfg="$READY/$slug/game.ingest.yaml"
    if [[ ! -f "$cfg" ]]; then
      echo "--- no game.ingest.yaml after process-wasabi; skip ingest ---"
      continue
    fi
    status="$(ledger_status_for_slug "$slug")"
    if [[ "$status" == "complete" ]]; then
      echo "SKIP ledger complete after stage: $slug"
      register_omeka "$slug"
      continue
    fi
  else
    echo "--- already staged; ingest pending ---"
  fi

  echo "--- ingest (BagIt + Wasabi preservation) $slug ---"
  if remote_intake_archiver_compose run --rm -T ingest-worker "/data/gdrive-intake/ready/$slug/game.ingest.yaml"; then
    echo "=== done ingest $slug $(date -Is) ==="
    register_omeka "$slug"
  else
    echo "=== FAILED ingest $slug $(date -Is) ==="
  fi
done

echo "=== Wasabi Redump per-folder pipeline finished $(date -Is) ==="
