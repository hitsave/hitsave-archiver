#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

docker compose run --rm --entrypoint python ingest-worker \
  /app/scripts/build-dip-e-ark.py /config/preservation/pilot-game.yaml

TAR="/tank/hitsave-archiver/output/dip/pilot-wog1.tar"
TITLE="$(python3 -c "import yaml; print(yaml.safe_load(open('config/preservation/pilot-game.yaml'))['omeka_item_title'])")"

cd omeka-test
docker compose exec -T omeka php /scripts/ensure-omeka-test-site.php
docker compose exec -T omeka php /scripts/create-dip-example-item.php "/dip-output/pilot-wog1.tar" "$TITLE"
