#!/usr/bin/env bash
# SSOT: scripts/dump_nointro.py → hitsave-intake submission_info.py
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-$ROOT/../hitsave-intake/src/intake/nointro/submission_info.py}"
SRC="$ROOT/scripts/dump_nointro.py"
if [[ ! -f "$SRC" ]]; then
  echo "Missing SSOT parser: $SRC" >&2
  exit 1
fi
mkdir -p "$(dirname "$TARGET")"
{
  echo '#!/usr/bin/env python3'
  echo '"""Parse No-Intro sidecars — synced from hitsave-archiver/scripts/dump_nointro.py."""'
  echo "# Run: hitsave-archiver/scripts/sync-nointro-parser-to-intake.sh"
  echo
  tail -n +3 "$SRC"
} > "$TARGET"
chmod +x "$TARGET" 2>/dev/null || true
echo "Synced $SRC → $TARGET"
