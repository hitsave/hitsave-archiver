#!/usr/bin/env bash
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env
ARCHIVER="$(remote_intake_archiver_root)"
GEN="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-$ARCHIVER/.generated}"
mkdir -p "$GEN"

# Poll Drive once (Google Drive API via intake) and print "folder_id<TAB>ready_slug" lines.

POLL_JSON="${POLL_JSON:-$GEN/gdrive-redump-poll.json}"

remote_intake_compose_intake run --rm -T --user 1000:1000 intake poll --once -v --config-root /config \
  > "$POLL_JSON"

python3 - <<'PY' "$POLL_JSON"
import json, re, sys
# Lane -> slug prefix (same as hitsave-intake intake.lanes). The lane folder decides the sidecar
# rules, so there's no disc-image filter here any more.
PREFIX = {"redump": "redump", "no-intro": "nointro", "others": "others"}
data = json.load(open(sys.argv[1], encoding="utf-8"))
for pkg in data.get("packages") or []:
    fid = pkg.get("id")
    name = pkg.get("name") or ""
    lane = pkg.get("lane") or "redump"
    if not fid or name.strip().lower() == "processed" or lane not in PREFIX:
        continue
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    slug = slug or "disc"
    print(f"{fid}\t{lane}\t{PREFIX[lane]}-{slug}")
PY
