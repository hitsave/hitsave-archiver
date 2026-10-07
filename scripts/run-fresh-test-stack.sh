#!/usr/bin/env bash
# Wipe test Omeka, reset ledger batch rows, re-ingest + upload with draft/item-set/Moby.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BATCH="${1:-config/preservation/batch.yml}"
BATCH_KEY="$(python3 -c "import yaml; from pathlib import Path; print(yaml.safe_load(Path('$BATCH').read_text())['batch_key'])")"

bash scripts/ensure-local-config.sh
python3 scripts/sync-preservation-config.py
python3 scripts/sync-docker-config.py
python3 scripts/batch-expand-preservation.py "$BATCH"

docker compose up -d postgres clamav status-web
docker compose exec -T postgres psql -U hitsave -d hitsave_ledger < schema/002_moby_ledger.sql || true

cd omeka-test
docker compose up -d omeka mariadb
docker compose exec -T -u root omeka php /scripts/ensure-press-item-set.php
docker compose exec -T -u root omeka php /scripts/wipe-omeka-test-content.php
cd "$ROOT"

docker compose run --rm --entrypoint python ingest-worker /app/scripts/reset-batch-ledger.py "$BATCH_KEY"
bash scripts/run-batch-resume-omeka.sh "$BATCH"

echo "Status dashboard: http://127.0.0.1:8090/"
echo "Omeka admin (private drafts): http://127.0.0.1:8088/admin"
