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
  mkdir -p "$SUBMISSIONS_ROOT"/{incoming,workspace,processed,failed} "$OUTPUT_ROOT" "$ROOT/.ci/empty" \
    "${HOST_INTAKE:-$ROOT/.ci/intake}/ready"
  export HOST_INTAKE="${HOST_INTAKE:-$ROOT/.ci/intake}"
  # Throwaway ledger for CI (the compose bind mount refuses a missing dir by design).
  export HOST_LEDGER_DATA="${HOST_LEDGER_DATA:-$ROOT/.ci/ledger}"
  mkdir -p "$HOST_LEDGER_DATA"
  HITSAVE_PRIVATE_CONFIG="$HITSAVE_PRIVATE_CONFIG" "$ROOT/scripts/bootstrap-private-config-from-examples.sh" --force
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

# A case passes only for the RIGHT reason. A reject must exit 1 (ingest-submission.py's
# SystemExit) and print a line matching the expected rejection (any line: stdout and stderr
# interleave, so the message is not reliably last); anything else (compose or
# container failure, traceback, ClamAV not ready, a different check firing) is a harness FAIL,
# so a broken environment can never make reject cases pass vacuously.
run_case() {
  local name="$1"
  local zip="$2"
  local expect="$3"  # reject | accept
  local pattern="$4" # ERE some output line must match
  local sub_cfg="${5:-/config/preservation/submissions.yaml}"
  local base out code hit
  base="$(basename "$zip")"
  cp -f "$zip" "$INCOMING/test-$base"
  set +e
  out="$("${COMPOSE[@]}" run --rm --entrypoint python ingest-worker \
    /app/scripts/ingest-submission.py "/data/submissions/incoming/test-$base" \
    --submissions-config "$sub_cfg" \
    --skip-ingest 2>&1)"
  code=$?
  set -e
  rm -f "$INCOMING/test-$base"
  if [[ "$expect" == "reject" ]]; then
    if [[ $code -ne 1 ]]; then
      echo "FAIL $name: expected rejection (exit 1), got exit $code"
      printf '%s\n' "$out"
      return 1
    fi
  elif [[ $code -ne 0 ]]; then
    echo "FAIL $name: expected accept, got exit $code"
    printf '%s\n' "$out"
    return 1
  fi
  if ! hit="$(grep -Em1 -- "$pattern" <<<"$out")"; then
    echo "FAIL $name: exit $code as expected, but for the wrong reason"
    echo "     expected a line matching: $pattern"
    printf '%s\n' "$out"
    return 1
  fi
  if [[ "$expect" == "reject" ]]; then
    echo "OK   $name (rejected: ${hit})"
  else
    echo "OK   $name (accepted: ${hit})"
  fi
  return 0
}

# Self-test: an empty file with a deliberately wrong expected reason exits 1 but must be
# reported as FAIL. This is the shape of the old vacuous pass (any non-zero exit counted).
selftest_out="$(run_case "harness self-test" /dev/null reject '^Virus detected' 2>&1 || true)"
if [[ "$selftest_out" != FAIL* ]]; then
  echo "Harness self-test failed: a broken case was reported as passing:" >&2
  echo "$selftest_out" >&2
  exit 1
fi
echo "OK   harness self-test (broken case is reported as FAIL)"

NESTED='^Nested archive not allowed in submission zip: '
fail=0
run_case "EICAR in zip" "$FIX/eicar_com.zip" reject '^Virus detected in .*: Eicar-Test-Signature' || fail=1
run_case "EICAR double zip" "$FIX/eicarcom2.zip" reject "${NESTED}'eicar_com\.zip'" || fail=1
run_case "Snyk zip-slip" "$FIX/zip-slip.zip" reject "^Unsafe zip path segment in '(\.\./)+tmp/evil\.txt'" || fail=1
run_case "HEXAQA zip-slip" "$FIX/zip-slip-traversal.zip" reject "^Unsafe zip path segment in '(\.\./)+tmp/qa-zip-slip\.txt'" || fail=1
run_case "Extension allowlist" "$FIX/allowlist-exe.zip" reject '^Extension not allowed: .*/run\.exe \(\.exe\)$' || fail=1
run_case "Nested inner zip" "$FIX/nested-inner-zip.zip" reject "${NESTED}'payload\.zip'" || fail=1
run_case "Extract bytes cap" "$FIX/extract-bytes-cap.zip" reject '^Extracted total exceeds max_extracted_bytes' \
  "/fixtures/submissions-security/ci-tiny-limits.yaml" || fail=1
run_case "Valid minimal" "$FIX/valid-minimal.zip" accept '\(--skip-ingest\)$' || fail=1
run_case "Zip bomb 42.zip" "$BOMBS/42.zip" reject "${NESTED}'lib 0\.zip'" || fail=1
run_case "Zip bomb philkatz.zip" "$BOMBS/philkatz.zip" reject "^Special file type in zip: " || fail=1

if [[ $fail -ne 0 ]]; then
  echo "One or more fixture cases failed." >&2
  exit 1
fi
echo "All submission security fixture cases passed."
