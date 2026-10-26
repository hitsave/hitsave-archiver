#!/usr/bin/env bash
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env
ARCHIVER="$(remote_intake_archiver_root)"
GEN="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-$ARCHIVER/.generated}"
mkdir -p "$GEN"

# Poll Wasabi once (S3 API via gdrive-intake); print "source_prefix<TAB>package_folder_name<TAB>ready_slug".

POLL_JSON="${POLL_JSON:-$GEN/wasabi-redump-poll.json}"

remote_intake_compose_gdrive_intake run --rm -T --user 1000:1000 gdrive-intake poll-wasabi --once -v --config-root /config \
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
