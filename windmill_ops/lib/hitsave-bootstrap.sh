#!/usr/bin/env bash
# Stable entry for Windmill bash jobs (bind-mounted at /run/hitsave/hitsave-bootstrap.sh).
# Worker env vars may not reach the job; worker-paths.env carries ARCHIVER_HOST_PATH.
set -euo pipefail

if [[ -f /run/hitsave/worker-paths.env ]]; then
  # shellcheck disable=SC1091
  source /run/hitsave/worker-paths.env
fi

_hitsave_env=""
for _root in "${ARCHIVER_HOST_PATH:-}" /archiver; do
  [[ -z "$_root" ]] && continue
  _candidate="${_root}/windmill_ops/lib/hitsave-env.sh"
  if [[ -f "$_candidate" ]]; then
    _hitsave_env="$_candidate"
    break
  fi
done
unset _root _candidate

if [[ -z "$_hitsave_env" ]]; then
  echo "hitsave-bootstrap: hitsave-env.sh not found (ARCHIVER_HOST_PATH=${ARCHIVER_HOST_PATH:-unset})" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$_hitsave_env"
unset _hitsave_env
