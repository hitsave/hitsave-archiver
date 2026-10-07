#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

bash scripts/build-fixture-dips.sh

cd omeka-test
docker compose exec -T omeka php /scripts/ensure-omeka-test-site.php
docker compose exec -T omeka php /scripts/create-dip-example-item.php "$@"
