#!/usr/bin/env bash
# Bootstrap operator config: private repo (secrets + database).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PRIVATE="${HITSAVE_PRIVATE_CONFIG:-$ROOT/../hitsave-archiver-config}"

copy_if_missing() {
  local example="$1"
  local target="$2"
  if [[ -f "$target" ]]; then
    return 0
  fi
  if [[ ! -f "$example" ]]; then
    echo "Missing $example (cannot create $target)" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$target")"
  cp "$example" "$target"
  echo "Created $target from $(basename "$example") — edit before use on a network."
}

mkdir -p "$PRIVATE/secrets" "$PRIVATE/preservation"

copy_if_missing "$ROOT/config/secrets/wasabi-credentials.yaml.example" \
  "$PRIVATE/secrets/wasabi-credentials.yaml"
copy_if_missing "$ROOT/config/secrets/mobygames-credentials.yaml.example" \
  "$PRIVATE/secrets/mobygames-credentials.yaml"
copy_if_missing "$ROOT/config/secrets/omeka-api-credentials-local.yaml.example" \
  "$PRIVATE/secrets/omeka-api-credentials-local.yaml"
copy_if_missing "$ROOT/config/secrets/omeka-api-credentials-prod.yaml.example" \
  "$PRIVATE/secrets/omeka-api-credentials-prod.yaml"
copy_if_missing "$ROOT/config/preservation/database.yaml.example" "$PRIVATE/preservation/database.yaml"
copy_if_missing "$ROOT/config/preservation/database.yaml.example" "$ROOT/config/preservation/database.yaml"

HITSAVE_PRIVATE_CONFIG="$PRIVATE" python3 "$ROOT/scripts/sync-preservation-config.py"

echo "Private config root: $PRIVATE"
echo "Local Omeka test stack: clone hitsave-omeka-test (HITSAVE_OMEKA_TEST_ROOT)."
