#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/windmill_ops/docker/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT/windmill_ops/docker/.env.example" "$ENV_FILE"
  echo "Created $ENV_FILE — set ARCHIVER_HOST_PATH and HITSAVE_PRIVATE_CONFIG_HOST, then re-run."
  exit 1
fi
exec docker compose -f "$ROOT/windmill_ops/docker/docker-compose.yml" --env-file "$ENV_FILE" up -d "$@"
