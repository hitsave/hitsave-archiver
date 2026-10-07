#!/usr/bin/env bash
# Run portable submission security fixtures; expect reject or accept per fixtures/submissions-security/README.md
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FIX="$ROOT/fixtures/submissions-security"
BOMBS="$FIX/bombs"

if [[ -n "${CI:-}" || ! -d /tank/hitsave-archiver/submissions ]]; then
  export SUBMISSIONS_ROOT="${SUBMISSIONS_ROOT:-$ROOT/.ci/submissions}"
  export OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT/.ci/output}"
  export HITSAVE_PRIVATE_CONFIG="${HITSAVE_PRIVATE_CONFIG:-$ROOT/.ci/private-config}"
  mkdir -p "$SUBMISSIONS_ROOT"/{incoming,workspace,processed,failed} "$OUTPUT_ROOT" "$ROOT/.ci/empty"
  HITSAVE_PRIVATE_CONFIG="$HITSAVE_PRIVATE_CONFIG" python3 "$ROOT/scripts/sync-preservation-config.py"
  COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.ci.yml)
else
  COMPOSE=(docker compose)
fi

INCOMING="${SUBMISSIONS_INCOMING:-${SUBMISSIONS_ROOT:-/tank/hitsave-archiver/submissions}/incoming}"

if [[ ! -d "$FIX" ]]; then
  echo "Missing $FIX — run scripts/generate-submission-security-fixtures.py" >&2
  exit 1
fi

bash "$ROOT/scripts/fetch-submission-security-bombs.sh"
python3 "$ROOT/scripts/generate-submission-security-fixtures.py" >/dev/null

mkdir -p "$INCOMING"
"${COMPOSE[@]}" build ingest-worker >/dev/null

run_case() {
  local name="$1"
  local zip="$2"
  local expect="$3" # reject | accept
  local sub_cfg="${4:-/config/preservation/submissions.yaml}"
  local base
  base="$(basename "$zip")"
  cp -f "$zip" "$INCOMING/test-$base"
  set +e
  out="$("${COMPOSE[@]}" run --rm --entrypoint python ingest-worker \
    /app/scripts/ingest-submission.py "/data/submissions/incoming/test-$base" \
    --submissions-config "$sub_cfg" \
    --game-key "fixture-test-${base%.zip}" --skip-ingest 2>&1)"
  code=$?
  set -e
  rm -f "$INCOMING/test-$base"
  if [[ "$expect" == "reject" ]]; then
    if [[ $code -eq 0 ]]; then
      echo "FAIL $name: expected reject, got success"
      echo "$out"
      return 1
    fi
    reason="$(echo "$out" | tail -1)"
    echo "OK   $name (rejected: ${reason})"
  else
    if [[ $code -ne 0 ]]; then
      echo "FAIL $name: expected accept, exit $code"
      echo "$out"
      return 1
    fi
    echo "OK   $name (accepted as expected)"
  fi
  return 0
}

fail=0
run_case "EICAR in zip" "$FIX/eicar_com.zip" reject || fail=1
run_case "EICAR double zip" "$FIX/eicarcom2.zip" reject || fail=1
run_case "Snyk zip-slip" "$FIX/zip-slip.zip" reject || fail=1
run_case "HEXAQA zip-slip" "$FIX/zip-slip-traversal.zip" reject || fail=1
run_case "Extension allowlist" "$FIX/allowlist-exe.zip" reject || fail=1
run_case "Nested inner zip" "$FIX/nested-inner-zip.zip" reject || fail=1
run_case "Extract bytes cap" "$FIX/extract-bytes-cap.zip" reject "/fixtures/submissions-security/ci-tiny-limits.yaml" || fail=1
run_case "Valid minimal" "$FIX/valid-minimal.zip" accept || fail=1
run_case "Zip bomb 42.zip" "$BOMBS/42.zip" reject || fail=1
run_case "Zip bomb philkatz.zip" "$BOMBS/philkatz.zip" reject || fail=1

if [[ $fail -ne 0 ]]; then
  echo "One or more fixture cases failed." >&2
  exit 1
fi
echo "All submission security fixture cases passed."
