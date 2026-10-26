#!/usr/bin/env bash
set -euo pipefail
RUN_DIR="$(mktemp -d)"
trap 'rm -rf "$RUN_DIR"' EXIT
cp -a /workspace/. "$RUN_DIR/"
if [[ -f "$RUN_DIR/wmill.docker.yaml" ]]; then
  cp "$RUN_DIR/wmill.docker.yaml" "$RUN_DIR/wmill.yaml"
fi
cd "$RUN_DIR"
exec wmill "$@"
