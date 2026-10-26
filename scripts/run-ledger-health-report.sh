#!/usr/bin/env bash
# Read-only ledger report. Uses database.yaml (no ingest-worker container).
#
# On the preservation host (or Windmill worker on the compose network):
#   export HITSAVE_PRIVATE_CONFIG=/path/to/hitsave-archiver-config
#   ./scripts/run-ledger-health-report.sh [--search foo] [--limit 20]
#
# If postgres host in database.yaml is only reachable inside compose (e.g. host: postgres),
# run from a host on that network — not from a laptop without a port publish.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export HITSAVE_PRIVATE_CONFIG="${HITSAVE_PRIVATE_CONFIG:-$ROOT/../hitsave-archiver-config}"
exec python3 "$ROOT/scripts/ledger_health_report.py" "$@"
