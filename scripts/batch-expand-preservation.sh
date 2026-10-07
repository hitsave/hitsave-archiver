#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BATCH="${1:-config/preservation/batch.yml}"
docker compose run --rm --entrypoint python ingest-worker \
  /app/scripts/batch-expand-preservation.py "/config/preservation/${BATCH#config/preservation/}"
