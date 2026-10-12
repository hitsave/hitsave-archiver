#!/usr/bin/env python3
"""Upload a DIP .tar to Wasabi (single object per game_key)."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

try:
    import boto3
    import yaml
    from botocore.config import Config
    from botocore.exceptions import ClientError
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIP_WASABI = Path("/config/preservation/dip-wasabi.yml")
DEFAULT_DIP_WASABI_EXAMPLE = REPO_ROOT / "config/preservation/dip-wasabi.yml.example"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def resolve_config_path(raw: str, config_root: Path) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return config_root / path


def dip_wasabi_config_path(explicit: Path | None = None) -> Path:
    if explicit and explicit.is_file():
        return explicit
    if DEFAULT_DIP_WASABI.is_file():
        return DEFAULT_DIP_WASABI
    if DEFAULT_DIP_WASABI_EXAMPLE.is_file():
        return DEFAULT_DIP_WASABI_EXAMPLE
    raise RuntimeError(
        "Missing dip-wasabi config; copy config/preservation/dip-wasabi.yml.example to dip-wasabi.yml"
    )


def load_dip_wasabi_settings(cfg_path: Path) -> tuple[dict, Path]:
    root = load_yaml(cfg_path)
    wasabi = root.get("dip_wasabi") or {}
    if not wasabi:
        raise RuntimeError(f"No dip_wasabi: section in {cfg_path}")
    config_root = Path("/config") if Path("/config").is_dir() else REPO_ROOT / "config"
    cred_path = resolve_config_path(str(wasabi["credentials_file"]), config_root)
    if not cred_path.is_file():
        raise RuntimeError(f"Missing Wasabi credentials: {cred_path}")
    creds = load_yaml(cred_path)
    for field in ("access_key_id", "secret_access_key"):
        if not creds.get(field) or str(creds[field]).startswith("REPLACE"):
            raise RuntimeError(f"Set {field} in {cred_path}")
    return wasabi, cred_path


def make_s3_client(wasabi: dict, creds: dict):
    return boto3.client(
        "s3",
        endpoint_url=str(wasabi["endpoint"]).rstrip("/"),
        region_name=str(wasabi.get("region", "us-east-1")),
        aws_access_key_id=str(creds["access_key_id"]),
        aws_secret_access_key=str(creds["secret_access_key"]),
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def object_key(prefix: str, game_key: str, tar_name: str) -> str:
    base = prefix.strip("/")
    if base:
        return f"{base}/{game_key}/{tar_name}"
    return f"{game_key}/{tar_name}"


def omeka_external_tar_path(wasabi: dict, game_key: str, tar_name: str) -> str:
    mount_root = str(wasabi.get("omeka_mount_root") or "/dip-wasabi").rstrip("/")
    return f"{mount_root}/{game_key}/{tar_name}"


def upload_dip_tar(
    tar_path: Path,
    game_key: str,
    *,
    cfg_path: Path | None = None,
) -> dict[str, str | int]:
    tar_path = tar_path.resolve()
    if not tar_path.is_file():
        raise RuntimeError(f"DIP tar not found: {tar_path}")

    wasabi, _ = load_dip_wasabi_settings(dip_wasabi_config_path(cfg_path))
    bucket = str(wasabi["bucket"])
    key = object_key(str(wasabi.get("prefix", "")), game_key, tar_path.name)
    verify = bool(wasabi.get("verify_upload", True))
    creds = load_yaml(
        resolve_config_path(
            str(wasabi["credentials_file"]),
            Path("/config") if Path("/config").is_dir() else REPO_ROOT / "config",
        )
    )
    client = make_s3_client(wasabi, creds)
    size = tar_path.stat().st_size
    local_digest = sha256_file(tar_path) if verify else None

    with tar_path.open("rb") as body:
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentLength=size)

    if verify:
        head = client.head_object(Bucket=bucket, Key=key)
        remote_size = int(head.get("ContentLength") or 0)
        if remote_size != size:
            raise RuntimeError(f"Size mismatch after upload: s3://{bucket}/{key}")
        # Full-object GET verify is avoided for large DIPs (multi-GB tar).
        max_full_verify = int(wasabi.get("max_full_verify_bytes", 524288000))
        if local_digest and size <= max_full_verify:
            resp = client.get_object(Bucket=bucket, Key=key)
            remote = resp["Body"].read()
            if hashlib.sha256(remote).hexdigest() != local_digest:
                raise RuntimeError(f"SHA-256 mismatch after upload: s3://{bucket}/{key}")

    s3_uri = f"s3://{bucket}/{key}"
    external = omeka_external_tar_path(wasabi, game_key, tar_path.name)
    print(f"Uploaded {s3_uri} ({size} bytes)")
    print(f"Omeka external_tar_path (when mounted): {external}")
    return {
        "bucket": bucket,
        "key": key,
        "s3_uri": s3_uri,
        "external_tar_path": external,
        "tar_bytes": size,
        "sha256": local_digest or "",
    }


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(
            "Usage: upload-dip-wasabi.py <dip.tar> <game_key> [dip-wasabi.yml]"
        )
    tar_path = Path(sys.argv[1])
    game_key = sys.argv[2]
    cfg_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    try:
        upload_dip_tar(tar_path, game_key, cfg_path=cfg_path)
    except ClientError as e:
        raise SystemExit(f"Wasabi S3 error: {e}") from e


if __name__ == "__main__":
    main()
