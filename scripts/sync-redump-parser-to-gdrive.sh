#!/usr/bin/env bash
# SSOT: scripts/dump_redump.py → hitsave-gdrive-intake submission_info.py
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-$ROOT/../hitsave-gdrive-intake/src/intake/redump/submission_info.py}"
SRC="$ROOT/scripts/dump_redump.py"
if [[ ! -f "$SRC" ]]; then
  echo "Missing SSOT parser: $SRC" >&2
  exit 1
fi
if [[ ! -d "$(dirname "$TARGET")" ]]; then
  echo "GDrive intake tree not found (expected $TARGET)" >&2
  exit 1
fi
{
  echo '#!/usr/bin/env python3'
  echo '"""Parse Redump submissionInfo.txt — synced from hitsave-archiver/scripts/dump_redump.py."""'
  echo "# Run: hitsave-archiver/scripts/sync-redump-parser-to-gdrive.sh"
  echo
  tail -n +3 "$SRC"
} > "$TARGET"
chmod +x "$TARGET" 2>/dev/null || true
echo "Synced $SRC → $TARGET"
