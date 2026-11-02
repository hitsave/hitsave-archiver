#!/usr/bin/env bash
# Run windmill-cli in Docker (no host npm). Example: ./scripts/windmill-wmill.sh --version
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_DIR="$ROOT/windmill_ops/docker"
# Windmill compose env lives in the private config repo (windmill/.env). The usual setup is a
# symlink windmill_ops/docker/.env -> that file, so plain `docker compose` in that folder works.
PRIVATE_ENV="${HITSAVE_PRIVATE_CONFIG:-$ROOT/../hitsave-archiver-config}/windmill/.env"
ENV_FILE="$COMPOSE_DIR/.env"
if [[ ! -f "$ENV_FILE" && -f "$PRIVATE_ENV" ]]; then
  ENV_FILE="$PRIVATE_ENV"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE (and $PRIVATE_ENV) — see windmill_ops/README.md." >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

exec docker compose -f "$COMPOSE_DIR/docker-compose.yml" --env-file "$ENV_FILE" \
  --profile cli run --rm windmill_cli "$@"
