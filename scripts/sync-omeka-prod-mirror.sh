#!/usr/bin/env bash
# Pull prod Omeka config (read-only API) and apply to local omeka-test.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 scripts/pull-omeka-prod-mirror.py

cd omeka-test
docker compose build omeka
docker compose up -d omeka
sleep 6
docker compose exec -T -u root omeka php /scripts/apply-omeka-prod-mirror.php
docker compose restart omeka

echo "Test site: see config/omeka-test/settings.yaml public_url + /s/hitsave-test"
