#!/usr/bin/env bash
# Read-only rclone mount of hitsave-incoming (whole bucket) for Omeka external tar paths.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export HITSAVE_PRIVATE_CONFIG="${HITSAVE_PRIVATE_CONFIG:-$ROOT/../hitsave-archiver-config}"
MOUNT="${DIP_WASABI_MOUNT:-/mnt/hitsave-dip-wasabi}"
CONFIG="${1:-config/preservation/dip-wasabi.yml}"

REMOTE=$(python3 "$ROOT/scripts/render-rclone-dip-wasabi.py" "$CONFIG" | awk '/^Mount remote:/ {print $3}')

mkdir -p "$MOUNT"
if mountpoint -q "$MOUNT" 2>/dev/null; then
  echo "Already mounted: $MOUNT"
  exit 0
fi

echo "Mounting $REMOTE -> $MOUNT (read-only)"
# --allow-other needs user_allow_other in /etc/fuse.conf (or run as root). Omeka pilot
# prefers in-container mount (hitsave-omeka-test docker-compose.dip-wasabi-pilot.yml).
EXTRA=()
if grep -q '^user_allow_other' /etc/fuse.conf 2>/dev/null; then
  EXTRA=(--allow-other)
fi
exec rclone mount "$REMOTE" "$MOUNT" \
  --config "$ROOT/.generated/rclone-dip-wasabi.conf" \
  --read-only \
  --dir-cache-time 30s \
  --attr-timeout 30s \
  --vfs-cache-mode minimal \
  "${EXTRA[@]}" \
  --daemon
