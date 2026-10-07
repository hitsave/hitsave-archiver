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
python3 scripts/batch-expand-preservation.py "$BATCH"

PRIVATE="${HITSAVE_PRIVATE_CONFIG:-../hitsave-archiver-config}"
if [[ ! -f "$PRIVATE/secrets/omeka-api-credentials-local.yaml" ]]; then
  echo "Create $PRIVATE/secrets/omeka-api-credentials-local.yaml (see config/secrets/*.example)" >&2
  exit 1
fi

docker compose up -d postgres clamav
sleep 3

MANIFEST="config/preservation/generated/$(python3 -c "import yaml; from pathlib import Path; print(yaml.safe_load(Path('$BATCH').read_text())['batch_key'])")/manifest.yaml"
mapfile -t CONFIGS < <(python3 -c "
import yaml
from pathlib import Path
m = yaml.safe_load(Path('$MANIFEST').read_text())
for g in m['games']:
    print(g['config'])
")

exec bash "$ROOT/scripts/run-batch-resume-omeka.sh" "$BATCH"
