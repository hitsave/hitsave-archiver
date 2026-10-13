#!/usr/bin/env bash
# Materialize a private-config tree from config/**/*.example (operator SSOT).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

FORCE=0
TARGET=""

usage() {
  echo "Usage: bootstrap-private-config-from-examples.sh [--force] [TARGET_DIR]" >&2
  echo "  Default TARGET_DIR: \$HITSAVE_PRIVATE_CONFIG or $ROOT/.ci/private-config" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      FORCE=1
      shift
      ;;
    -h | --help)
      usage
      ;;
    *)
      if [[ -n "$TARGET" ]]; then
        usage
      fi
      TARGET="$1"
      shift
      ;;
  esac
done

if [[ -z "$TARGET" ]]; then
  TARGET="${HITSAVE_PRIVATE_CONFIG:-$ROOT/.ci/private-config}"
fi

copy_example() {
  local example="$1"
  local dest="$2"
  if [[ ! -f "$example" ]]; then
    echo "Missing example template: $example" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$dest")"
  if [[ "$FORCE" -eq 1 ]] || [[ ! -f "$dest" ]]; then
    cp "$example" "$dest"
    echo "Materialized $dest from $(basename "$example")"
  fi
}

mkdir -p "$TARGET/secrets" "$TARGET/preservation"

copy_example "$ROOT/config/secrets/wasabi-credentials.yaml.example" \
  "$TARGET/secrets/wasabi-credentials.yaml"
copy_example "$ROOT/config/secrets/mobygames-credentials.yaml.example" \
  "$TARGET/secrets/mobygames-credentials.yaml"
copy_example "$ROOT/config/secrets/omeka-api-credentials-local.yaml.example" \
  "$TARGET/secrets/omeka-api-credentials-local.yaml"
copy_example "$ROOT/config/secrets/omeka-api-credentials-prod.yaml.example" \
  "$TARGET/secrets/omeka-api-credentials-prod.yaml"
copy_example "$ROOT/config/preservation/database.yaml.example" \
  "$TARGET/preservation/database.yaml"

echo "Private config root: $TARGET"
