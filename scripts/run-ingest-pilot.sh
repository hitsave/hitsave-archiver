#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 scripts/sync-preservation-config.py

if [[ ! -d /tank/hitsave-archiver/output ]]; then
  echo "Create output root first (needs write access to /tank):" >&2
  echo "  sudo mkdir -p /tank/hitsave-archiver/output/{dip,aip,processed,quarantine}" >&2
  echo "  sudo chown -R \"$(whoami):$(whoami)\" /tank/hitsave-archiver" >&2
  exit 1
fi

docker compose up -d postgres clamav
echo "Waiting for Postgres..."
sleep 5
docker compose run --rm ingest-worker /config/preservation/pilot-game.yaml
