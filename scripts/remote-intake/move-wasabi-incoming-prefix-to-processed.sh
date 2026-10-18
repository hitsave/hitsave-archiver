#!/usr/bin/env bash
# MOVE each package folder under one Wasabi incoming prefix → processed/{prefix}/{name}/.
# Usage: move-wasabi-incoming-prefix-to-processed.sh --prefix <incoming-root> [--execute]
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env
ARCHIVER="$(remote_intake_archiver_root)"
GEN="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-$ARCHIVER/.generated}"
mkdir -p "$GEN"
INTAKE_CFG="${HITSAVE_PRIVATE_CONFIG}/preservation/wasabi-redump-intake.yaml"
INTAKE_MOUNT="/tmp/wasabi-redump-intake.yaml"
LOG="$GEN/move-wasabi-incoming-prefix-to-processed.log"
EXECUTE=false
INCOMING_PREFIX=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute) EXECUTE=true; shift ;;
    --prefix)
      INCOMING_PREFIX="${2%/}/"
      shift 2
      ;;
    *) echo "usage: $0 --prefix <wasabi-incoming-root> [--execute]" >&2; exit 2 ;;
  esac
done
if [[ -z "$INCOMING_PREFIX" ]]; then
  echo "usage: $0 --prefix <wasabi-incoming-root> [--execute]" >&2
  exit 2
fi

exec > >(tee -a "$LOG") 2>&1
echo "=== Wasabi ${INCOMING_PREFIX} → processed/ started $(date -Is) execute=$EXECUTE ==="

mapfile -t PKGS < <(
  cd "$ARCHIVER" && docker compose run --rm --no-deps \
    -v "$ARCHIVER/scripts:/app/scripts:ro" \
    -v "${HITSAVE_PRIVATE_CONFIG}/secrets:/config/secrets:ro" \
    -v "${HITSAVE_PRIVATE_CONFIG}/preservation/ingest.yaml:/config/preservation/ingest.yaml:ro" \
    --entrypoint python ingest-worker - "$INCOMING_PREFIX" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "/app/scripts")
import boto3
from botocore.config import Config
from upload_bag_wasabi import load_wasabi_settings
from yaml_config import load_yaml
from preservation_common import wasabi_s3_region

root = sys.argv[1]
if not root.endswith("/"):
    root += "/"
wasabi, _ = load_wasabi_settings(Path("/config/preservation/ingest.yaml"))
creds = load_yaml(Path("/config/secrets/wasabi-credentials.yaml"))
client = boto3.client(
    "s3",
    endpoint_url=str(wasabi["endpoint"]).rstrip("/"),
    region_name=wasabi_s3_region(wasabi),
    aws_access_key_id=str(creds["access_key_id"]),
    aws_secret_access_key=str(creds["secret_access_key"]),
    config=Config(signature_version="s3v4"),
)
for page in client.get_paginator("list_objects_v2").paginate(
    Bucket="hitsave-incoming", Prefix=root, Delimiter="/"
):
    for cp in page.get("CommonPrefixes") or []:
        name = cp["Prefix"][len(root) :].strip("/")
        if name:
            print(f"{root}{name}")
PY
)

echo "packages: ${#PKGS[@]}"
ok=0
fail=0
for pkg in "${PKGS[@]}"; do
  echo ""
  echo "-------- $(date -Is) $pkg --------"
  args=(/app/scripts/remote-intake/move_wasabi_package_by_keys.py "$pkg"
    --intake-config "$INTAKE_MOUNT")
  if [[ "$EXECUTE" == true ]]; then
    args+=(--execute --overwrite --try-prefix-first)
  fi
  if cd "$ARCHIVER" && docker compose run --rm --no-deps \
    -v "$ARCHIVER/scripts:/app/scripts:ro" \
    -v "$INTAKE_CFG:$INTAKE_MOUNT:ro" \
    --entrypoint python ingest-worker "${args[@]}"; then
    ((ok+=1)) || true
  else
    echo "FAILED $pkg"
    ((fail+=1)) || true
  fi
done

echo "=== finished $(date -Is) ok=$ok fail=$fail (of ${#PKGS[@]}) ==="
[[ "$fail" -eq 0 ]]
