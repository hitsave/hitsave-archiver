#!/usr/bin/env python3
"""Delete legacy single-object AIP keys press-material/{letter}/{package_id}.tar on Wasabi."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import boto3
    import yaml
    from botocore.config import Config
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INGEST = Path("/config/preservation/ingest.yaml")
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import importlib.util

_spec = importlib.util.spec_from_file_location("upload_dump_wasabi", REPO_ROOT / "scripts" / "upload-dump-wasabi.py")
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
load_wasabi_settings = _mod.load_wasabi_settings


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def is_legacy_tar_key(key: str, letter: str, prefix_root: str) -> bool:
    base = f"{prefix_root.strip('/')}/{letter.strip('/')}/"
    if not key.startswith(base):
        return False
    if not key.endswith(".tar"):
        return False
    rest = key[len(base) :]
    return "/" not in rest


def delete_legacy_tars(client, bucket: str, letter: str, prefix_root: str, *, dry_run: bool) -> int:
    scan_prefix = f"{prefix_root.strip('/')}/{letter.strip('/')}/"
    deleted = 0
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": scan_prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents") or []:
            key = str(obj["Key"])
            if not is_legacy_tar_key(key, letter, prefix_root):
                continue
            if dry_run:
                print(f"  would delete s3://{bucket}/{key}")
            else:
                client.delete_object(Bucket=bucket, Key=key)
                print(f"  deleted s3://{bucket}/{key}")
            deleted += 1
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return deleted


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: delete-wasabi-letter-tar-keys.py <sort_letter> [--dry-run] [ingest.yaml]"
        )
    letter = sys.argv[1].strip().upper()
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[2:] if a != "--dry-run"]
    ingest_path = Path(args[0]) if args else DEFAULT_INGEST
    if not ingest_path.is_file():
        ingest_path = REPO_ROOT / "config" / "preservation" / "ingest.yaml"
    ingest = load_yaml(ingest_path)
    prefix_root = str((ingest.get("wasabi") or {}).get("prefix") or "press-material")
    wasabi, _ = load_wasabi_settings(ingest_path)
    cred_path = Path(ingest_path).resolve().parent.parent / str(wasabi["credentials_file"])
    creds = load_yaml(cred_path)
    client = boto3.client(
        "s3",
        endpoint_url=str(wasabi["endpoint"]).rstrip("/"),
        region_name=str(wasabi.get("region", "us-east-1")),
        aws_access_key_id=str(creds["access_key_id"]),
        aws_secret_access_key=str(creds["secret_access_key"]),
        config=Config(signature_version="s3v4"),
    )
    from preservation_common import resolve_wasabi_bucket

    bucket = resolve_wasabi_bucket(wasabi, "press_material")
    mode = "dry-run" if dry_run else "delete"
    print(f"{mode}: legacy *.tar under s3://{bucket}/{prefix_root}/{letter}/")
    n = delete_legacy_tars(client, bucket, letter, prefix_root, dry_run=dry_run)
    print(f"Done. {'Would remove' if dry_run else 'Removed'} {n} tar object(s).")


if __name__ == "__main__":
    main()
