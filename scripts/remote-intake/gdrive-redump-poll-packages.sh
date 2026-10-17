#!/usr/bin/env bash
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env
ARCHIVER="$(remote_intake_archiver_root)"
GDRIVE="$(remote_intake_gdrive_root)"
BUNDLE="$(remote_intake_bundle_root)"
GEN="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-$ARCHIVER/.generated}"
mkdir -p "$GEN"

# Poll Drive once and print "folder_id<TAB>ready_slug" lines to stdout.

POLL_JSON="${POLL_JSON:-$GEN/gdrive-redump-poll.json}"


cd "$GDRIVE"

docker compose run --rm -T --user 1000:1000 gdrive-intake poll --once -v --config-root /config \
  > "$POLL_JSON"

python3 - <<'PY' "$POLL_JSON"
import json, re, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
prefix = "redump"
for pkg in data.get("packages") or []:
    fid = pkg.get("id")
    name = pkg.get("name") or ""
    if not fid:
        continue
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    print(f"{fid}\t{prefix}-{slug}")
PY
