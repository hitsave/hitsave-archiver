#!/usr/bin/env bash
# Register ledger-complete DIPs in Omeka (batch manifest) via omeka-catalog — no re-ingest.
# Does not re-run ingest-worker; use run-batch-resume-omeka.sh for full ingest + catalog register.
#
# Requires: source host.env (HITSAVE_PRIVATE_CONFIG, HOST_OUTPUT, …), test Omeka up,
# generated manifest under config/preservation/generated/<batch_key>/manifest.yaml.
#
# Usage:
#   source /path/to/hitsave-archiver-config/host.env
#   export HOST_OUTPUT=/path/to/existing/aip-dip-output   # optional
#   ./scripts/register-manifest-dips-omeka.sh config/preservation/batch-repcopies-s.yml
#   ./scripts/register-manifest-dips-omeka.sh --pilot   # pilot-wog1 only
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="${HITSAVE_REGISTER_LOG:-$ROOT/.generated/register-manifest-dips-omeka.log}"
mkdir -p "$(dirname "$LOG")"

register_manifest() {
  local manifest="$1"
  local label="$2"
  echo "===== $label =====" | tee -a "$LOG"
  while IFS=$'\t' read -r key cfg || [[ -n "${key:-}" ]]; do
    [[ -z "${key:-}" ]] && continue
    echo "--- omeka catalog register: $key ($(date -Is)) ---" | tee -a "$LOG"
    if docker compose run --rm -T omeka-catalog "$key" "/config/preservation/${cfg#config/preservation/}" </dev/null >>"$LOG" 2>&1; then
      echo "OK $key" | tee -a "$LOG"
    else
      echo "FAILED $key (see $LOG)" | tee -a "$LOG"
    fi
  done < <(
    python3 -c "
import yaml
from pathlib import Path
m = yaml.safe_load(Path('$manifest').read_text())
for g in m['games']:
    print(g['game_key'] + '\t' + g['config'])
"
  )
}

docker compose build omeka-catalog >/dev/null

if [[ "${1:-}" == "--pilot" ]]; then
  echo "Register run started $(date -Is)" | tee -a "$LOG"
  echo "--- omeka catalog register: pilot-wog1 ---" | tee -a "$LOG"
  docker compose run --rm -T omeka-catalog pilot-wog1 /config/preservation/pilot-game.yaml </dev/null >>"$LOG" 2>&1 \
    || echo "FAILED pilot-wog1" | tee -a "$LOG"
  echo "Register run finished $(date -Is)" | tee -a "$LOG"
  exit 0
fi

BATCH="${1:?Usage: register-manifest-dips-omeka.sh <batch.yml> | --pilot}"
BATCH_KEY="$(python3 -c "import yaml; from pathlib import Path; print(yaml.safe_load(Path('$BATCH').read_text())['batch_key'])")"
MANIFEST="config/preservation/generated/${BATCH_KEY}/manifest.yaml"
if [[ ! -f "$MANIFEST" ]]; then
  echo "Missing $MANIFEST; run: ./scripts/batch-expand-preservation.sh $BATCH" >&2
  exit 1
fi

echo "Register run started $(date -Is) batch=$BATCH_KEY HOST_OUTPUT=${HOST_OUTPUT:-./data/output}" | tee -a "$LOG"
register_manifest "$MANIFEST" "$BATCH_KEY"
echo "Register run finished $(date -Is)" | tee -a "$LOG"
