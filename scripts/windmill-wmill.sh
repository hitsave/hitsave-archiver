#!/usr/bin/env bash
# Run windmill-cli in Docker (no host npm). Example: ./scripts/windmill-wmill.sh --version
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_DIR="$ROOT/windmill_ops/docker"
ENV_FILE="$COMPOSE_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy from .env.example and set paths." >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

exec docker compose -f "$COMPOSE_DIR/docker-compose.yml" --env-file "$ENV_FILE" \
  --profile cli run --rm windmill_cli "$@"
