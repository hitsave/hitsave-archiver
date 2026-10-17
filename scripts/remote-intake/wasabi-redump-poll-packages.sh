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

# Poll Wasabi once; print "source_prefix<TAB>package_folder_name<TAB>ready_slug" (prefix "." = bucket root).

POLL_JSON="${POLL_JSON:-$GEN/wasabi-redump-poll.json}"



cd "$GDRIVE"

docker compose run --rm -T --user 1000:1000 gdrive-intake poll-wasabi --once -v --config-root /config \
  > "$POLL_JSON"

python3 - <<'PY' "$POLL_JSON"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for pkg in data.get("packages") or []:
    src = (pkg.get("source_prefix") or "").strip()
    if not src:
        src = "."
    name = pkg.get("package_folder_name") or ""
    slug = pkg.get("ready_slug") or ""
    if name and slug:
        print(f"{src}\t{name}\t{slug}")
PY
