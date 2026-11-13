#!/usr/bin/env bash
# Deploy main to saturn once its checks pass. The hitsave-deploy container runs this every
# 5 minutes (see docs/deploy-on-merge.md).
#
# Saturn only reads from GitHub: it fetches main and asks for the commit's check results.
# Nothing on GitHub can start anything here.
#
# Each run:
#   1. fetches main and stops if it is the commit deployed last;
#   2. stops if that commit already failed to deploy (fix it with a new commit, or run
#      deploy-saturn.sh by hand);
#   3. asks GitHub for the commit's checks: waits while they run, never deploys a commit whose
#      checks failed, and gives a commit with no checks 10 minutes for them to appear;
#   4. runs deploy-saturn.sh from that commit.
set -euo pipefail

repo="hitsave/hitsave-archiver"
dir="${HITSAVE_ARCHIVER_DIR:?set HITSAVE_ARCHIVER_DIR to the live hitsave-archiver checkout}"
here="$(cd "$(dirname "$0")" && pwd)"
ci_status="${HITSAVE_DEPLOY_CI_STATUS:-python3 $here/ci_status.py}"
no_checks_wait="${HITSAVE_DEPLOY_NO_CHECKS_WAIT_SECONDS:-600}"
state="$dir/.generated/deploy"
mkdir -p "$state"

git -C "$dir" fetch --quiet origin main
target="$(git -C "$dir" rev-parse origin/main)"
deployed="$(cat "$state/deployed-commit" 2>/dev/null || git -C "$dir" rev-parse HEAD)"

if [[ "$target" == "$deployed" ]]; then
  exit 0
fi
if [[ "$(cat "$state/failed-commit" 2>/dev/null)" == "$target" ]]; then
  exit 0
fi

# The repo is public, so reading its checks needs no token (60 requests an hour; this uses 12).
ci="$(curl --fail --silent --show-error --max-time 60 \
  --header "Accept: application/vnd.github+json" --user-agent hitsave-deploy \
  "https://api.github.com/repos/$repo/commits/$target/check-runs?per_page=100" | $ci_status)"
case "$ci" in
  pending)
    exit 0
    ;;
  failed)
    echo "Not deploying ${target:0:7}: its checks failed on GitHub."
    echo "$target" >"$state/failed-commit"
    exit 0
    ;;
  none)
    now="$(date +%s)"
    read -r seen_commit seen_at 2>/dev/null <"$state/first-seen" || true
    if [[ "${seen_commit:-}" != "$target" ]]; then
      echo "$target $now" >"$state/first-seen"
      seen_at="$now"
    fi
    if ((now - seen_at < no_checks_wait)); then
      exit 0
    fi
    ;;
  passed) ;;
  *)
    echo "Unexpected check result '$ci' for ${target:0:7}" >&2
    exit 1
    ;;
esac

# Run the deploy script from the commit being deployed, not from the old checkout.
script="$(mktemp)"
trap 'rm -f "$script"' EXIT
git -C "$dir" show "$target:scripts/deploy/deploy-saturn.sh" >"$script"
bash "$script" "$target"
