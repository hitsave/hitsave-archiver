#!/usr/bin/env bash
# Read-only ledger report (SSOT: scripts/ledger_health_report.py on preservation Postgres).
set -euo pipefail
# shellcheck disable=SC1091
source /archiver/windmill_ops/lib/hitsave-env.sh
export HITSAVE_PRIVATE_CONFIG="${HITSAVE_PRIVATE_CONFIG:-/config/private}"

args=( --limit "${limit:-50}" )
if [[ -n "${search:-}" ]]; then
  args+=( --search "$search" )
fi

exec python3 /archiver/scripts/ledger_health_report.py "${args[@]}"
