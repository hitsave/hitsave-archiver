#!/usr/bin/env bash
# Push windmill_ops/wmill to the local Windmill instance (scripts + remote_intake_sweep schedule).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_DIR="$ROOT/windmill_ops/docker"
ENV_FILE="$COMPOSE_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy from .env.example and set WMILL_TOKEN." >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

if [[ -z "${WMILL_TOKEN:-}" ]]; then
  echo "Set WMILL_TOKEN in $ENV_FILE (Windmill UI → Account → Tokens)." >&2
  exit 1
fi

BASE="${WINDMILL_CLI_BASE_URL:-http://windmill_server:8000}"
WS="${WINDMILL_WORKSPACE_ID:-hitsave-archiver}"

WMILL_ARGS=(
  --base-url "$BASE"
  --token "$WMILL_TOKEN"
  --workspace "$WS"
)

docker compose -f "$COMPOSE_DIR/docker-compose.yml" --env-file "$ENV_FILE" --profile cli run --rm windmill_cli \
  sync push --yes --skip-branch-validation "${WMILL_ARGS[@]}" "$@"

docker compose -f "$COMPOSE_DIR/docker-compose.yml" --env-file "$ENV_FILE" --profile cli run --rm windmill_cli \
  schedule push schedules/remote_intake_sweep.schedule.yaml f/hitsave/remote_intake_sweep "${WMILL_ARGS[@]}"
