#!/usr/bin/env python3
"""Delete all objects under a Wasabi/S3 prefix (e.g. press-material/F/)."""
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

from preservation_common import wasabi_bucket_for_storage_prefix, wasabi_s3_region  # noqa: E402


from yaml_config import load_yaml  # noqa: E402


def purge_prefix(client, bucket: str, prefix: str) -> int:
    prefix = prefix.strip("/") + "/"
    deleted = 0
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        contents = resp.get("Contents") or []
        if not contents:
            break
        batch = [{"Key": obj["Key"]} for obj in contents]
        client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
        deleted += len(batch)
        print(f"  deleted {deleted} objects…", flush=True)
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return deleted


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: purge-wasabi-prefix.py <s3_prefix> [ingest.yaml] [bucket]  "
            "e.g. press-material/F  or  dumps  hitsave-dumps"
        )
    prefix = sys.argv[1].strip("/")
    ingest_path = DEFAULT_INGEST
    bucket_override = None
    for arg in sys.argv[2:]:
        candidate = Path(arg)
        if candidate.suffix in (".yaml", ".yml") or candidate.is_file():
            ingest_path = candidate
        else:
            bucket_override = str(arg).strip()
    if not ingest_path.is_file():
        ingest_path = REPO_ROOT / "config" / "preservation" / "ingest.yaml"
    wasabi, _ = load_wasabi_settings(ingest_path)
    cred_path = Path(ingest_path).resolve().parent.parent / str(wasabi["credentials_file"])
    creds = load_yaml(cred_path)
    client = boto3.client(
        "s3",
        endpoint_url=str(wasabi["endpoint"]).rstrip("/"),
        region_name=wasabi_s3_region(wasabi),
        aws_access_key_id=str(creds["access_key_id"]),
        aws_secret_access_key=str(creds["secret_access_key"]),
        config=Config(signature_version="s3v4"),
    )
    if bucket_override:
        bucket = bucket_override
    else:
        bucket = wasabi_bucket_for_storage_prefix(wasabi, prefix)
    print(f"Purging s3://{bucket}/{prefix}/ …")
    n = purge_prefix(client, bucket, prefix)
    print(f"Done. Removed {n} objects under {prefix}/")


if __name__ == "__main__":
    main()
