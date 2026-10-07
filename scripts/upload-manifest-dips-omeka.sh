#!/usr/bin/env bash
# Upload existing DIP .tar files (batch manifest) to test/prod Omeka via omeka-uploader.
# Does not re-run ingest-worker; use run-batch-resume-omeka.sh for full ingest + upload.
#
# Requires: source host.env (HITSAVE_PRIVATE_CONFIG, HOST_OUTPUT, …), test Omeka up,
# generated manifest under config/preservation/generated/<batch_key>/manifest.yaml.
#
# Usage:
#   source ~/hitsave-archiver-config/host.env
#   export HOST_OUTPUT=/tank/hitsave-archiver/output   # optional: reuse tank DIPs
#   ./scripts/upload-manifest-dips-omeka.sh config/preservation/batch-repcopies-s.yml
#   ./scripts/upload-manifest-dips-omeka.sh --pilot   # pilot-wog1 only
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="${HITSAVE_UPLOAD_LOG:-$ROOT/.generated/upload-manifest-dips-omeka.log}"
mkdir -p "$(dirname "$LOG")"

upload_manifest() {
  local manifest="$1"
  local label="$2"
  echo "===== $label =====" | tee -a "$LOG"
  while IFS=$'\t' read -r key cfg || [[ -n "${key:-}" ]]; do
    [[ -z "${key:-}" ]] && continue
    echo "--- omeka upload: $key ($(date -Is)) ---" | tee -a "$LOG"
    if docker compose run --rm -T omeka-uploader "$key" "/config/preservation/${cfg#config/preservation/}" </dev/null >>"$LOG" 2>&1; then
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

docker compose build omeka-uploader >/dev/null

if [[ "${1:-}" == "--pilot" ]]; then
  echo "Upload run started $(date -Is)" | tee -a "$LOG"
  echo "--- omeka upload: pilot-wog1 ---" | tee -a "$LOG"
  docker compose run --rm -T omeka-uploader pilot-wog1 /config/preservation/pilot-game.yaml </dev/null >>"$LOG" 2>&1 \
    || echo "FAILED pilot-wog1" | tee -a "$LOG"
  echo "Upload run finished $(date -Is)" | tee -a "$LOG"
  exit 0
fi

BATCH="${1:?Usage: upload-manifest-dips-omeka.sh <batch.yml> | --pilot}"
BATCH_KEY="$(python3 -c "import yaml; from pathlib import Path; print(yaml.safe_load(Path('$BATCH').read_text())['batch_key'])")"
MANIFEST="config/preservation/generated/${BATCH_KEY}/manifest.yaml"
if [[ ! -f "$MANIFEST" ]]; then
  echo "Missing $MANIFEST; run: ./scripts/batch-expand-preservation.sh $BATCH" >&2
  exit 1
fi

echo "Upload run started $(date -Is) batch=$BATCH_KEY HOST_OUTPUT=${HOST_OUTPUT:-./data/output}" | tee -a "$LOG"
upload_manifest "$MANIFEST" "$BATCH_KEY"
echo "Upload run finished $(date -Is)" | tee -a "$LOG"
