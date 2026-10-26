#!/usr/bin/env bash
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env
ARCHIVER="$(remote_intake_archiver_root)"
GEN="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-$ARCHIVER/.generated}"
mkdir -p "$GEN"

# Poll Drive once (Google Drive API via gdrive-intake) and print "folder_id<TAB>ready_slug" lines.

POLL_JSON="${POLL_JSON:-$GEN/gdrive-redump-poll.json}"

remote_intake_compose_gdrive_intake run --rm -T --user 1000:1000 gdrive-intake poll --once -v --config-root /config \
  > "$POLL_JSON"

python3 - <<'PY' "$POLL_JSON"
import json, re, sys

DISC_SUFFIXES = (".iso", ".bin", ".img", ".chd", ".gdi")


def has_disc_image(pkg: dict) -> bool:
    for f in pkg.get("files") or []:
        n = str(f.get("name") or "").lower()
        if any(n.endswith(s) for s in DISC_SUFFIXES):
            return True
    return False


data = json.load(open(sys.argv[1], encoding="utf-8"))
prefix = "redump"
for pkg in data.get("packages") or []:
    fid = pkg.get("id")
    name = pkg.get("name") or ""
    if not fid or name in ("processed",):
        continue
    if not has_disc_image(pkg):
        print(f"SKIP (Drive folder has no disc image; use Wasabi if duplicate): {name}", file=sys.stderr)
        continue
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    print(f"{fid}\t{prefix}-{slug}")
PY
