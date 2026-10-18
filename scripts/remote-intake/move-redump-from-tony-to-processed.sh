#!/usr/bin/env bash
# MOVE each package under hitsave-incoming/redump-from-tony/ → processed/redump-from-tony/{name}/
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
LOG="$GEN/move-redump-from-tony-to-processed.log"
EXECUTE=false
if [[ "${1:-}" == "--execute" ]]; then
  EXECUTE=true
fi

exec > >(tee -a "$LOG") 2>&1
echo "=== redump-from-tony → processed/ started $(date -Is) execute=$EXECUTE ==="

mapfile -t PKGS < <(
  cd "$ARCHIVER" && docker compose run --rm --no-deps \
    -v "$ARCHIVER/scripts:/app/scripts:ro" \
    -v /home/jonas/hitsave-e2e/hitsave-archiver-config/secrets:/config/secrets:ro \
    -v "$ARCHIVER/config/preservation/ingest.yaml:/config/preservation/ingest.yaml:ro" \
    --entrypoint python ingest-worker - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "/app/scripts")
import boto3
from botocore.config import Config
from upload_bag_wasabi import load_wasabi_settings
from yaml_config import load_yaml
from preservation_common import wasabi_s3_region

wasabi, _ = load_wasabi_settings(Path("/config/preservation/ingest.yaml"))
creds = load_yaml(Path("/config/secrets/wasabi-credentials.yaml"))
client = boto3.client("s3", endpoint_url=str(wasabi["endpoint"]).rstrip("/"),
    region_name=wasabi_s3_region(wasabi),
    aws_access_key_id=str(creds["access_key_id"]),
    aws_secret_access_key=str(creds["secret_access_key"]),
    config=Config(signature_version="s3v4"))
root = "redump-from-tony/"
for page in client.get_paginator("list_objects_v2").paginate(
    Bucket="hitsave-incoming", Prefix=root, Delimiter="/"
):
    for cp in page.get("CommonPrefixes") or []:
        name = cp["Prefix"][len(root):].strip("/")
        if name:
            print(f"redump-from-tony/{name}")
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
