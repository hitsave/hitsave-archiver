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
# intake names each package by its dump type (ready_slug: dvd-video-…, switch-…, others-…; see
# hitsave-intake intake.naming). Older intake images don't report it: fall back to the lane prefix.
PREFIX = {"redump": "redump", "no-intro": "nointro", "others": "others"}
data = json.load(open(sys.argv[1], encoding="utf-8"))
for pkg in data.get("packages") or []:
    fid = pkg.get("id")
    name = pkg.get("name") or ""
    lane = pkg.get("lane") or "redump"
    if not fid or name.strip().lower() == "processed" or lane not in PREFIX:
        continue
    ready = str(pkg.get("ready_slug") or "").strip()
    if not ready:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower() or "disc"
        ready = f"{PREFIX[lane]}-{slug}"
    if pkg.get("naming_note"):
        print(f"WARN: {name}: {pkg['naming_note']}", file=sys.stderr)
    print(f"{fid}\t{lane}\t{ready}")
PY
