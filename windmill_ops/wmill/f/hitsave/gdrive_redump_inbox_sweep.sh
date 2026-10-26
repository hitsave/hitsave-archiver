#!/usr/bin/env bash
# Google Drive redump inbox: poll → stage → ingest → prod Omeka (per package).
# shellcheck disable=SC1091
source /archiver/windmill_ops/lib/hitsave-env.sh
export HITSAVE_POLL_TRIGGER="${HITSAVE_POLL_TRIGGER:-windmill}"
exec "$ARCHIVER_ROOT/scripts/remote-intake/run-gdrive-redump-per-folder.sh"
