#!/usr/bin/env bash
# Bootstrap operator config: private repo (secrets + database).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PRIVATE="${HITSAVE_PRIVATE_CONFIG:-$ROOT/../hitsave-archiver-config}"

HITSAVE_PRIVATE_CONFIG="$PRIVATE" "$ROOT/scripts/bootstrap-private-config-from-examples.sh" "$PRIVATE"

# Legacy: some tools still read database.yaml from the public config tree.
if [[ ! -f "$ROOT/config/preservation/database.yaml" ]]; then
  cp "$ROOT/config/preservation/database.yaml.example" "$ROOT/config/preservation/database.yaml"
  echo "Created $ROOT/config/preservation/database.yaml from database.yaml.example"
fi

HITSAVE_PRIVATE_CONFIG="$PRIVATE" python3 "$ROOT/scripts/sync-preservation-config.py"

echo "Example file map: docs/operator-config.md"
echo "Host paths: copy config/host.env.example → \$PRIVATE/host.env, edit, source before compose."
echo "Local Omeka test stack: clone hitsave-omeka-test (see that repo docs/build-real-dip.md)."
