#!/usr/bin/env bash
# Full remote intake automation: Wasabi lane then Drive lane (poll + ingest + Omeka each).
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh
RI="$ARCHIVER_ROOT/scripts/remote-intake"
# Hold the shared intake lock across both lanes (per-folder scripts see it as already held).
# shellcheck source=../../../../scripts/remote-intake/common.sh
source "$RI/common.sh"
remote_intake_acquire_lock

echo "=== remote intake sweep $(date -Is) ==="
echo "--- Wasabi incoming ---"
"$RI/run-wasabi-redump-per-folder.sh"
echo "--- Google Drive redump ---"
"$RI/run-gdrive-redump-per-folder.sh"
echo "=== remote intake sweep finished $(date -Is) ==="
