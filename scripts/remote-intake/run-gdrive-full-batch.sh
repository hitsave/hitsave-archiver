#!/usr/bin/env bash
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env
remote_intake_acquire_lock
ARCHIVER="$(remote_intake_archiver_root)"
GDRIVE="$(remote_intake_gdrive_root)"
BUNDLE="$(remote_intake_bundle_root)"
GEN="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-$ARCHIVER/.generated}"
mkdir -p "$GEN"

# Rebuild images, then per-folder stage→ingest, then prod Omeka reconcile pass.
# Per-folder stage→ingest→Omeka; final register-prod-dumps-omeka.sh reconciles misses.
# Usage: run-gdrive-full-batch.sh [--skip-build]
BUNDLE="$(remote_intake_bundle_root)"
LOG="$GEN/run-gdrive-full-batch.log"
SKIP_BUILD=false
if [[ "${1:-}" == "--skip-build" ]]; then
  SKIP_BUILD=true
fi

exec > >(tee -a "$LOG") 2>&1
echo "=== full GDrive Redump batch started $(date -Is) skip_build=$SKIP_BUILD ==="

if [[ "$SKIP_BUILD" != true ]]; then
  echo "=== docker build gdrive-intake $(date -Is) ==="
  cd "$ROOT/hitsave-gdrive-intake"
  
  docker compose build gdrive-intake

  echo "=== docker build archiver ingest + omeka-catalog $(date -Is) ==="
  cd "$ROOT/hitsave-archiver"
  
  docker compose build ingest-worker omeka-catalog
fi

echo "=== per-folder stage + ingest $(date -Is) ==="
"$RI/run-gdrive-redump-per-folder.sh"

echo "=== prod Omeka dump catalog $(date -Is) ==="
"$RI/register-prod-dumps-omeka.sh"

echo "=== full GDrive Redump batch finished $(date -Is) ==="
