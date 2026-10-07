#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GAME_CFG="config/preservation/game.yml"
if [[ ! -f "$GAME_CFG" ]]; then
  echo "Copy config/preservation/game.yml.example to $GAME_CFG" >&2
  exit 1
fi

docker compose run --rm --entrypoint python ingest-worker \
  /app/scripts/build-dip-e-ark.py "/config/preservation/game.yml"

TAR="$(python3 -c "
import yaml
from pathlib import Path
p = yaml.safe_load(open('$GAME_CFG'))['output_tar']
# /output/dip/foo.tar -> /tank/hitsave-archiver/output/dip/foo.tar
print(str(Path('/tank/hitsave-archiver/output') / p.removeprefix('/output/').lstrip('/')))
")"
TITLE="$(python3 -c "import yaml; print(yaml.safe_load(open('$GAME_CFG'))['omeka_item_title'])")"

cd omeka-test
docker compose exec -T omeka php /scripts/ensure-omeka-test-site.php
docker compose exec -T omeka php /scripts/create-dip-example-item.php "/dip-output/pilot-wog1.tar" "$TITLE"
