#!/usr/bin/env python3
"""Render .generated/rclone-dip-wasabi.conf from dip-wasabi.yml + credentials."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "config/preservation/dip-wasabi.yml"
    if not cfg_path.is_file():
        cfg_path = REPO_ROOT / "config/preservation/dip-wasabi.yml.example"
    w = (yaml.safe_load(cfg_path.read_text()) or {}).get("dip_wasabi") or {}

    private = Path(os.environ.get("HITSAVE_PRIVATE_CONFIG", str(REPO_ROOT.parent / "hitsave-archiver-config")))
    cred_rel = Path(str(w.get("credentials_file", "secrets/wasabi-credentials.yaml")))
    cred_path = cred_rel if cred_rel.is_absolute() else private / "secrets" / cred_rel.name
    if not cred_path.is_file():
        cred_path = REPO_ROOT / "config" / cred_rel
    creds = yaml.safe_load(cred_path.read_text())

    endpoint = str(w["endpoint"]).replace("https://", "").replace("http://", "").rstrip("/")
    out_dir = REPO_ROOT / ".generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "rclone-dip-wasabi.conf"
    out.write_text(
        f"""[hitsave-dip-wasabi]
type = s3
provider = Wasabi
access_key_id = {creds["access_key_id"]}
secret_access_key = {creds["secret_access_key"]}
endpoint = {endpoint}
region = {w.get("region", "us-east-1")}
acl = private
no_check_bucket = true
""",
        encoding="utf-8",
    )
    bucket = str(w["bucket"])
    prefix = str(w.get("prefix", "")).strip("/")
    remote = f"hitsave-dip-wasabi:{bucket}/{prefix}" if prefix else f"hitsave-dip-wasabi:{bucket}"
    print(f"Wrote {out}")
    print(f"Mount remote: {remote}")


if __name__ == "__main__":
    main()
