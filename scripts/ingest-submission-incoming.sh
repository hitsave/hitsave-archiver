#!/usr/bin/env bash
# Process one or all zips in submissions/incoming (portable archivist drop zone).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

INCOMING="${SUBMISSIONS_INCOMING:-/tank/hitsave-archiver/submissions/incoming}"
shopt -s nullglob

if [[ $# -ge 1 ]]; then
  ZIPS=("$@")
else
  ZIPS=("$INCOMING"/*.zip)
fi

if [[ ${#ZIPS[@]} -eq 0 ]] || [[ ! -e "${ZIPS[0]:-}" ]]; then
  echo "No zip files to ingest (drop under $INCOMING or pass paths)." >&2
  exit 1
fi

docker compose build ingest-worker
INCOMING_ABS="$(cd "$INCOMING" && pwd)"
for z in "${ZIPS[@]}"; do
  src="$(realpath "$z")"
  base="$(basename "$src")"
  if [[ "$(dirname "$src")" != "$INCOMING_ABS" ]]; then
    cp -f "$src" "$INCOMING_ABS/$base"
  fi
  echo "=== Ingest submission: $base ==="
  docker compose run --rm --entrypoint python ingest-worker \
    /app/scripts/ingest-submission.py "/data/submissions/incoming/$base"
done
