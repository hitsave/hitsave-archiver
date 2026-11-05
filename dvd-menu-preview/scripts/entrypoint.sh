#!/bin/bash
set -euo pipefail
# VLC refuses to run as root; output volume may be root-owned from prior runs.
# /output must be a dedicated directory (compose mounts <HOST_OUTPUT>/dvd-menu there): it is chowned.
mkdir -p /output /tmp/runtime-preview
chown -R preview:preview /output /tmp/runtime-preview
exec python3 /app/extract_dvd_menu_preview.py "$@"
