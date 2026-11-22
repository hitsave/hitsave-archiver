#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=compose_shared.sh
source "$ROOT/scripts/compose_shared.sh"
BATCH="${1:-config/preservation/batch.yml}"
compose_run --rm --entrypoint python ingest-worker \
  /app/scripts/batch-expand-preservation.py "/config/preservation/${BATCH#config/preservation/}"
