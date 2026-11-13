#!/usr/bin/env bash
# Make Windmill match windmill_ops/wmill: push changed scripts and schedules, and delete any
# script or schedule under f/ that git no longer has. Variables, secrets and resources stay as
# they are. Pass --dry-run to only list the changes. The deploy job runs this after each merge.
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

docker compose -f "$COMPOSE_DIR/docker-compose.yml" --env-file "$ENV_FILE" --profile cli run --rm -T windmill_cli \
  sync push --yes --skip-branch-validation --include-schedules \
  --skip-variables --skip-secrets --skip-resources "${WMILL_ARGS[@]}" "$@"
