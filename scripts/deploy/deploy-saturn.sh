#!/usr/bin/env bash
# Deploy one commit of main to saturn. deploy-poll.sh runs this when main has a new commit
# whose checks passed; you can also run it by hand:
#
#   scripts/deploy/deploy-saturn.sh <commit>
#
# What it does, in order:
#   1. Waits for the remote-intake lock, so no sweep or prune is running, and holds it to the end.
#   2. Fast-forwards the live checkout (HITSAVE_ARCHIVER_DIR) to <commit>. It stops if the
#      checkout has uncommitted changes to tracked files.
#   3. Rebuilds the ingest-worker and omeka-catalog images when their inputs changed.
#   4. Makes Windmill match windmill_ops/wmill when a job file changed.
#   5. Deploys the new ledger image on luigi when the ledger's inputs changed.
#
# It compares <commit> with the last commit it deployed (.generated/deploy/deployed-commit),
# so a failed deploy is caught up by the next one. On failure it writes the commit to
# .generated/deploy/failed-commit, and the poller leaves that commit alone.
#
# It prints a short summary. The full output goes to .generated/deploy/<time>-<commit>.log.
set -euo pipefail

# Prints "images", "windmill" and/or "ledger", one per line, for the changed paths on stdin.
deploy_plan() {
  local images=0 windmill=0 ledger=0 path
  while IFS= read -r path; do
    case "$path" in
      ingest-worker/* | scripts/* | packages/* | docker-compose.yml) images=1 ;;
    esac
    case "$path" in
      windmill_ops/wmill/*) windmill=1 ;;
    esac
    # The paths that make the ledger-image workflow build a new image.
    case "$path" in
      ledger/* | config/* | .dockerignore | .github/workflows/ledger-image.yml) ledger=1 ;;
      scripts/*/*) ;;
      scripts/*.py) ledger=1 ;;
    esac
  done
  if ((images)); then echo images; fi
  if ((windmill)); then echo windmill; fi
  if ((ledger)); then echo ledger; fi
  return 0
}

main() {
  local commit="${1:?usage: deploy-saturn.sh <commit>}"
  local dir="${HITSAVE_ARCHIVER_DIR:?set HITSAVE_ARCHIVER_DIR to the live hitsave-archiver checkout}"
  local private="${HITSAVE_PRIVATE_CONFIG:-$dir/../hitsave-archiver-config}"
  local ledger_deploy="${HITSAVE_LEDGER_DEPLOY:-$dir/../bin/deploy-ledger-luigi.sh}"
  local lock="$dir/.generated/windmill/remote-intake.lock"
  local wait_s="${HITSAVE_DEPLOY_LOCK_WAIT_SECONDS:-7200}"
  local state="$dir/.generated/deploy"

  mkdir -p "$state"
  local log
  # <commit> may be a name such as origin/main, so keep only safe characters in the file name.
  log="$state/$(date -u +%Y%m%dT%H%M%SZ)-$(tr -c 'A-Za-z0-9\n' - <<<"${commit:0:12}").log"
  exec 3>&1 >>"$log" 2>&1
  say() { echo "$*" >&3; echo "== $*"; }
  fail() {
    echo "$commit" >"$state/failed-commit"
    say "FAILED: $*. Full log: $log"
    exit 1
  }

  cd "$dir"
  git fetch --quiet origin main || fail "git fetch failed"
  commit="$(git rev-parse --verify "$commit^{commit}")" || fail "unknown commit"
  say "Deploying ${commit:0:7}"

  # The lock file sits in a sticky folder, so open it read-only.
  exec 9<"$lock" || fail "cannot open the remote-intake lock"
  flock -w "$wait_s" 9 || fail "a sweep or prune held the remote-intake lock for over ${wait_s}s"
  say "Holding the remote-intake lock"

  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    fail "the live checkout has uncommitted changes to tracked files"
  fi
  local before
  before="$(cat "$state/deployed-commit" 2>/dev/null || git rev-parse HEAD)"
  if git merge-base --is-ancestor "$commit" "$before"; then
    say "Nothing to do: ${commit:0:7} is already deployed"
    return 0
  fi
  if [[ "$(git rev-parse HEAD)" != "$commit" ]]; then
    git merge --ff-only --quiet "$commit" || fail "cannot fast-forward the checkout to ${commit:0:7}"
  fi
  local changed
  changed="$(git diff --name-only "$before" "$commit")"
  say "Checkout at ${commit:0:7}; $(grep -c . <<<"$changed") files changed since ${before:0:7}"

  local plan
  plan="$(deploy_plan <<<"$changed")"

  if grep -qx images <<<"$plan"; then
    say "Rebuilding ingest-worker and omeka-catalog"
    (
      set -a
      # shellcheck disable=SC1091
      source "$private/host.env"
      set +a
      docker compose build ingest-worker omeka-catalog
    ) || fail "image build failed"
    say "Images rebuilt"
  else
    say "Images: no inputs changed"
  fi

  if grep -qx windmill <<<"$plan"; then
    say "Updating Windmill jobs"
    scripts/windmill-sync-push.sh || fail "wmill sync push failed"
    say "Windmill jobs match windmill_ops/wmill"
  else
    say "Windmill: no job files changed"
  fi

  if grep -qx ledger <<<"$plan"; then
    say "Deploying the ledger on luigi"
    "$ledger_deploy" || fail "ledger deploy failed"
    say "Ledger deployed: $(grep '^after:' "$log" | tail -1)"
  else
    say "Ledger: no inputs changed"
  fi

  echo "$commit" >"$state/deployed-commit"
  rm -f "$state/failed-commit"
  say "Done"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
