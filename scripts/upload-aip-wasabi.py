#!/usr/bin/env python3
"""Upload an unpacked E-ARK AIP directory to Wasabi S3; verify SHA-256 after each put."""
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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iter_package_files(package_root: Path) -> list[Path]:
    files = [p for p in sorted(package_root.rglob("*")) if p.is_file()]
    if not files:
        raise RuntimeError(f"No files in AIP directory: {package_root}")
    return files


def storage_prefix(prefix: str, game_key: str) -> str:
    base = prefix.strip("/")
    key_part = game_key.strip("/")
    if base:
        return f"{base}/{key_part}/"
    return f"{key_part}/"


def object_key(storage_root: str, bag_dir: Path, file_path: Path) -> str:
    rel = file_path.relative_to(bag_dir).as_posix()
    return f"{storage_root}{rel}"


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
        raise RuntimeError(
            f"Wasabi credentials not found: {cred_path} "
            f"(copy config/secrets/wasabi-credentials.yaml.example into private repo secrets/wasabi-credentials.yaml)"
        )
    creds = load_yaml(cred_path)
    for field in ("access_key_id", "secret_access_key"):
        if not creds.get(field) or str(creds[field]).startswith("REPLACE"):
            raise RuntimeError(f"Set {field} in {cred_path}")
    return wasabi, cred_path


def upload_bag_to_wasabi(
    bag_dir: Path,
    game_key: str,
    *,
    ingest_config_path: Path = DEFAULT_INGEST_CONFIG,
) -> str:
    bag_dir = bag_dir.resolve()
    if not bag_dir.is_dir():
        raise RuntimeError(f"Bag directory not found: {bag_dir}")

    wasabi, _ = load_wasabi_settings(ingest_config_path)
    if not wasabi.get("enabled", True):
        raise RuntimeError("Wasabi upload is disabled (wasabi.enabled: false)")

    bucket = str(wasabi["bucket"])
    root = storage_prefix(str(wasabi.get("prefix", "")), game_key)
    verify = bool(wasabi.get("verify_upload", True))
    creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), ingest_config_path.parent.parent))
    client = make_s3_client(wasabi, creds)

    files = iter_package_files(bag_dir)
    uploaded = 0
    for path in files:
        key = object_key(root, bag_dir, path)
        local_digest = sha256_file(path) if verify else None
        size = path.stat().st_size
        with path.open("rb") as body:
            client.put_object(Bucket=bucket, Key=key, Body=body, ContentLength=size)
        if verify:
            resp = client.get_object(Bucket=bucket, Key=key)
            remote = resp["Body"].read()
            if len(remote) != size:
                raise RuntimeError(f"Size mismatch after upload: s3://{bucket}/{key}")
            if sha256_bytes(remote) != local_digest:
                raise RuntimeError(f"SHA-256 mismatch after upload: s3://{bucket}/{key}")
        uploaded += 1
        print(f"  uploaded s3://{bucket}/{key} ({size} bytes)")

    print(f"Wasabi upload complete: {uploaded} objects under s3://{bucket}/{root}")
    return root


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def update_ledger_wasabi(
    game_key: str,
    wasabi_prefix: str,
    *,
    ingest_config_path: Path = DEFAULT_INGEST_CONFIG,
) -> None:
    ingest = load_yaml(ingest_config_path)
    db_path = Path(ingest["database"]["config_file"])
    db_cfg = load_yaml(db_path)["postgres"]
    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE game_ingest
                SET aip_wasabi_prefix = %s,
                    aip_uploaded_at = %s,
                    updated_at = NOW()
                WHERE game_key = %s
                """,
                (wasabi_prefix, datetime.now(timezone.utc), game_key),
            )
            if cur.rowcount == 0:
                raise RuntimeError(f"No game_ingest row for game_key={game_key!r}")
        conn.commit()


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(
            "Usage: upload-aip-wasabi.py <bag_dir> <game_key> [ingest.yaml]\n"
            "  Updates game_ingest.aip_wasabi_prefix when upload succeeds."
        )
    bag_dir = Path(sys.argv[1])
    game_key = sys.argv[2]
    ingest_path = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_INGEST_CONFIG

    try:
        prefix = upload_bag_to_wasabi(bag_dir, game_key, ingest_config_path=ingest_path)
        update_ledger_wasabi(game_key, prefix, ingest_config_path=ingest_path)
    except ClientError as e:
        raise SystemExit(f"Wasabi S3 error: {e}") from e


if __name__ == "__main__":
    main()
