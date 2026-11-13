#!/usr/bin/env bash
# Run the deploy poller every 5 minutes. A deploy can take a while (it waits for a running
# sweep, then builds images); the next check starts 5 minutes after it ends, so two deploys
# never overlap. The poller is read from the checkout each time, so a merged change to it
# takes effect on the next run.
set -uo pipefail
interval="${HITSAVE_DEPLOY_INTERVAL_SECONDS:-300}"
poller="${HITSAVE_ARCHIVER_DIR:?}/scripts/deploy/deploy-poll.sh"
echo "$(date -u +%FT%TZ) hitsave-deploy started: checking main every ${interval}s"
while true; do
  bash "$poller" 2>&1 | while IFS= read -r line; do echo "$(date -u +%FT%TZ) $line"; done
  sleep "$interval"
done
