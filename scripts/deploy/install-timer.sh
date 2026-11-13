#!/usr/bin/env bash
# Install the systemd user timer that deploys main to saturn. Run it once on saturn, as jonas,
# from the live hitsave-archiver checkout:
#
#   scripts/deploy/install-timer.sh
#
# It is safe to run again. See docs/deploy-on-merge.md.
set -euo pipefail

checkout="$(cd "$(dirname "$0")/../.." && pwd)"
units="$HOME/.config/systemd/user"
mkdir -p "$units"

cat >"$units/hitsave-deploy.service" <<EOF
[Unit]
Description=Deploy hitsave-archiver main to saturn once its checks pass
After=network-online.target docker.service

[Service]
Type=oneshot
Environment=HITSAVE_ARCHIVER_DIR=$checkout
ExecStart=$checkout/scripts/deploy/deploy-poll.sh
# A deploy waits up to 2 hours for a running sweep, then builds images.
TimeoutStartSec=4h
EOF

cat >"$units/hitsave-deploy.timer" <<EOF
[Unit]
Description=Check for a new hitsave-archiver main every 5 minutes

[Timer]
OnBootSec=5min
OnUnitInactiveSec=5min

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now hitsave-deploy.timer
systemctl --user list-timers hitsave-deploy.timer --no-pager
