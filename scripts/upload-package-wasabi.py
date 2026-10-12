#!/usr/bin/env python3
"""Upload a BagIt .tar (single AIP/DIP object) to Wasabi; update ledger package fields."""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import boto3
    import psycopg
    import yaml
    from botocore.config import Config
    from botocore.exceptions import ClientError
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INGEST_CONFIG = Path("/config/preservation/ingest.yaml")
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from preservation_common import wasabi_bagit_tar_key  # noqa: E402


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def resolve_config_path(raw: str, config_root: Path) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return config_root / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_s3_client(wasabi: dict, creds: dict):
    return boto3.client(
        "s3",
        endpoint_url=str(wasabi["endpoint"]).rstrip("/"),
        region_name=str(wasabi.get("region", "us-east-1")),
        aws_access_key_id=str(creds["access_key_id"]),
        aws_secret_access_key=str(creds["secret_access_key"]),
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )


def load_wasabi_settings(ingest_config_path: Path) -> tuple[dict, Path]:
    ingest = load_yaml(ingest_config_path)
    wasabi = ingest.get("wasabi") or {}
    if not wasabi:
        raise RuntimeError(f"No wasabi: section in {ingest_config_path}")
    config_root = ingest_config_path.parent.parent
    cred_path = resolve_config_path(str(wasabi["credentials_file"]), config_root)
    if not cred_path.is_file():
        raise RuntimeError(f"Wasabi credentials not found: {cred_path}")
    creds = load_yaml(cred_path)
    for field in ("access_key_id", "secret_access_key"):
        if not creds.get(field) or str(creds[field]).startswith("REPLACE"):
            raise RuntimeError(f"Set {field} in {cred_path}")
    return wasabi, cred_path


def upload_package_tar(
    tar_path: Path,
    game_key: str,
    *,
    ingest_config_path: Path = DEFAULT_INGEST_CONFIG,
) -> dict[str, str | int]:
    tar_path = tar_path.resolve()
    if not tar_path.is_file():
        raise RuntimeError(f"Package tar not found: {tar_path}")

    wasabi, _ = load_wasabi_settings(ingest_config_path)
    if not wasabi.get("enabled", True):
        raise RuntimeError("Wasabi upload is disabled (wasabi.enabled: false)")

    bucket = str(wasabi["bucket"])
    prefix = str(wasabi.get("prefix", ""))
    key = wasabi_bagit_tar_key(game_key, prefix)
    verify = bool(wasabi.get("verify_upload", True))
    creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), ingest_config_path.parent.parent))
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
        max_full_verify = int(wasabi.get("max_full_verify_bytes", 524288000))
        if local_digest and size <= max_full_verify:
            resp = client.get_object(Bucket=bucket, Key=key)
            remote = resp["Body"].read()
            if hashlib.sha256(remote).hexdigest() != local_digest:
                raise RuntimeError(f"SHA-256 mismatch after upload: s3://{bucket}/{key}")

    s3_uri = f"s3://{bucket}/{key}"
    print(f"Uploaded {s3_uri} ({size} bytes)")
    return {
        "bucket": bucket,
        "key": key,
        "s3_uri": s3_uri,
        "tar_bytes": size,
        "sha256": local_digest or sha256_file(tar_path),
    }


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def update_ledger_package(
    game_key: str,
    upload: dict[str, str | int],
    *,
    ingest_config_path: Path = DEFAULT_INGEST_CONFIG,
) -> None:
    ingest = load_yaml(ingest_config_path)
    db_path = Path(ingest["database"]["config_file"])
    db_cfg = load_yaml(db_path)["postgres"]
    now = datetime.now(timezone.utc)
    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE game_ingest
                SET aip_wasabi_prefix = %s,
                    aip_uploaded_at = %s,
                    package_tar_key = %s,
                    package_tar_sha256 = %s,
                    updated_at = NOW()
                WHERE game_key = %s
                """,
                (str(upload["s3_uri"]), now, str(upload["key"]), str(upload["sha256"]), game_key),
            )
            if cur.rowcount == 0:
                raise RuntimeError(f"No game_ingest row for game_key={game_key!r}")
        conn.commit()


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(
            "Usage: upload-package-wasabi.py <package.tar> <game_key> [ingest.yaml]"
        )
    tar_path = Path(sys.argv[1])
    game_key = sys.argv[2]
    ingest_path = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_INGEST_CONFIG
    try:
        upload = upload_package_tar(tar_path, game_key, ingest_config_path=ingest_path)
        update_ledger_package(game_key, upload, ingest_config_path=ingest_path)
    except ClientError as e:
        raise SystemExit(f"Wasabi S3 error: {e}") from e


if __name__ == "__main__":
    main()
