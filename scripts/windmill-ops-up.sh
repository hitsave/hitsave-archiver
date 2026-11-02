#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/windmill_ops/docker/.env"
PRIVATE="${HITSAVE_PRIVATE_CONFIG:-$ROOT/../hitsave-archiver-config}"
if [[ ! -e "$ENV_FILE" ]]; then
  if [[ ! -f "$PRIVATE/windmill/.env" ]]; then
    mkdir -p "$PRIVATE/windmill"
    cp "$ROOT/windmill_ops/docker/.env.example" "$PRIVATE/windmill/.env"
    cp "$ROOT/windmill_ops/docker/worker-paths.env.example" "$PRIVATE/windmill/worker-paths.env"
    echo "Created $PRIVATE/windmill/{.env,worker-paths.env} — set ARCHIVER_HOST_PATH, HITSAVE_PRIVATE_CONFIG_HOST, HITSAVE_INTAKE_HOST, then re-run."
    ln -s "$(cd "$PRIVATE" && pwd)/windmill/.env" "$ENV_FILE"
    exit 1
  fi
  ln -s "$(cd "$PRIVATE" && pwd)/windmill/.env" "$ENV_FILE"
  echo "Linked $ENV_FILE -> $PRIVATE/windmill/.env"
fi
exec docker compose -f "$ROOT/windmill_ops/docker/docker-compose.yml" --env-file "$ENV_FILE" up -d "$@"
