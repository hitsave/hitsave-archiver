#!/usr/bin/env bash
# Download zip bomb regression samples (ShiftLeftSecurity/zipdu). Use only in isolated ingest tests.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/fixtures/submissions-security/bombs"
mkdir -p "$DEST"
BASE="https://raw.githubusercontent.com/ShiftLeftSecurity/zipdu/master/bombs"
for f in 42.zip philkatz.zip; do
  if [[ -f "$DEST/$f" ]]; then
    echo "Have $DEST/$f"
    continue
  fi
  echo "Downloading $f ..."
  curl -fsSL -o "$DEST/$f" "$BASE/$f"
done
ls -lh "$DEST"
