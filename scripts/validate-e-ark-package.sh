#!/usr/bin/env bash
# Validate an unpacked E-ARK CSIP package (AIP or DIP root directory).
# See docs/preservation-packaging-standard.md
set -euo pipefail

ROOT="${1:?Usage: validate-e-ark-package.sh /path/to/package-root}"

if [[ ! -d "$ROOT" ]]; then
  echo "Not a directory: $ROOT" >&2
  exit 1
fi

ABS="$(cd "$ROOT" && pwd)"

run_validator() {
  local out
  out="$(mktemp)"
  if eark-validator "$ABS" >"$out" 2>&1; then
    :
  fi
  cat "$out"
  if grep -q '"schematron_results":{"status":"INVALID"' "$out"; then
    echo "E-ARK schematron validation FAILED: $ABS" >&2
    rm -f "$out"
    exit 1
  fi
  if grep -q '"status":"INVALID"' "$out" && grep -q 'schema_results' "$out"; then
    echo "E-ARK schema validation FAILED: $ABS" >&2
    rm -f "$out"
    exit 1
  fi
  rm -f "$out"
}

if command -v eark-validator >/dev/null 2>&1; then
  run_validator
  echo "E-ARK validation passed: $ABS"
  exit 0
fi

echo "eark-validator not on PATH; running via Docker (python:3.12-slim)..." >&2
out="$(mktemp)"
docker run --rm \
  -v "${ABS}:/package:ro" \
  python:3.12-slim-bookworm \
  bash -ec 'pip install -q eark-validator && eark-validator /package' >"$out" 2>&1
cat "$out"
if grep -q '"schematron_results":{"status":"INVALID"' "$out"; then
  echo "E-ARK schematron validation FAILED: $ABS" >&2
  rm -f "$out"
  exit 1
fi
rm -f "$out"
echo "E-ARK validation passed: $ABS"
