#!/usr/bin/env bash
# Build and start the hitsave-deploy container (deploy/docker-compose.yml). Run it on saturn
# from the live checkout. It is safe to run again: it rebuilds and recreates the container.
set -euo pipefail

checkout="$(cd "$(dirname "$0")/../.." && pwd)"
# Read only the two host paths from the Windmill env file; it also holds tokens.
env_file="$checkout/windmill_ops/docker/.env"
ARCHIVER_HOST_PATH="$(sed -n 's/^ARCHIVER_HOST_PATH=//p' "$env_file")"
HITSAVE_PRIVATE_CONFIG_HOST="$(sed -n 's/^HITSAVE_PRIVATE_CONFIG_HOST=//p' "$env_file")"
HITSAVE_BIN_HOST="$(cd "$checkout/.." && pwd)/bin"
DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
export ARCHIVER_HOST_PATH HITSAVE_PRIVATE_CONFIG_HOST HITSAVE_BIN_HOST DOCKER_GID

docker compose -f "$checkout/deploy/docker-compose.yml" up -d --build
docker logs --tail 5 hitsave-deploy
