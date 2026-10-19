#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BATCH="${1:-config/preservation/batch.yml}"
if [[ ! -f "$BATCH" ]]; then
  echo "Copy config/preservation/batch.yml.example to $BATCH and edit paths." >&2
  exit 1
fi

python3 scripts/sync-preservation-config.py
bash scripts/batch-expand-preservation.sh "$BATCH"

PRIVATE="${HITSAVE_PRIVATE_CONFIG:-../hitsave-archiver-config}"
if [[ ! -f "$PRIVATE/omeka-catalog.yaml" ]]; then
  echo "Missing $PRIVATE/omeka-catalog.yaml (production Omeka API target; see omeka-catalog.test.yaml for test)" >&2
  exit 1
fi
if [[ ! -f "$PRIVATE/secrets/omeka-api-credentials-prod.yaml" ]]; then
  echo "Create $PRIVATE/secrets/omeka-api-credentials-prod.yaml for archive.hitsave.org (see config/secrets/*.example)" >&2
  exit 1
fi

docker compose up -d postgres clamav
sleep 3

exec bash "$ROOT/scripts/run-batch-resume-omeka.sh" "$BATCH"
