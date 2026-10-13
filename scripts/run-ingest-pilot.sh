#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 scripts/sync-preservation-config.py

OUTPUT_HOST="$(python3 "$ROOT/scripts/read-host-path-config.py" output_root)"
if [[ ! -d "$OUTPUT_HOST" ]]; then
  echo "Create output root first (configure host_paths.output_root in config/preservation/host-paths.yaml):" >&2
  echo "  mkdir -p \"$OUTPUT_HOST\"/{dip,aip,processed,quarantine}" >&2
  exit 1
fi

docker compose up -d postgres clamav
echo "Waiting for Postgres..."
sleep 5
if [[ ! -f config/preservation/game.yml ]]; then
  echo "Copy config/preservation/game.yml.example to config/preservation/game.yml" >&2
  exit 1
fi
docker compose run --rm ingest-worker /config/preservation/game.yml
