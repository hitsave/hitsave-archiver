"""Upload unpacked BagIt trees to Wasabi (all ingest kinds; bucket/prefix from ledger + ingest-kinds)."""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import boto3
    import psycopg
    from boto3.s3.transfer import TransferConfig
    from botocore.config import Config
    from botocore.exceptions import ClientError
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INGEST_CONFIG = Path("/config/preservation/ingest.yaml")
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from config_require import require_bool  # noqa: E402
from dump_classify import classify_dump_sip, package_storage_s3_uri  # noqa: E402
from package_identity import relative_sip_path  # noqa: E402
from preservation_common import (  # noqa: E402
    material_root_for_kind,
    resolve_wasabi_bucket,
    sha256_file,
    wasabi_package_storage_prefix,
    wasabi_s3_region,
    wasabi_unpacked_prefix,
)
from yaml_config import load_yaml  # noqa: E402
from s3_etag import DEFAULT_PART_SIZE, FileHashes, etag_matches, hash_file  # noqa: E402

# Small files are also re-downloaded and SHA-256 checked; every file gets a HEAD size + ETag check.
VERIFY_DOWNLOAD_MAX_BYTES = 256 * 1024 * 1024

# threshold == chunksize == s3_etag.DEFAULT_PART_SIZE so the expected ETag is predictable.
S3_TRANSFER = TransferConfig(
    multipart_threshold=DEFAULT_PART_SIZE,
    multipart_chunksize=DEFAULT_PART_SIZE,
    max_concurrency=4,
)


def resolve_config_path(raw: str, config_root: Path) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return config_root / path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iter_package_files(package_root: Path) -> list[Path]:
    files = [p for p in sorted(package_root.rglob("*")) if p.is_file()]
    if not files:
        raise RuntimeError(f"No files in bag directory: {package_root}")
    return files


def object_key(storage_root: str, bag_dir: Path, file_path: Path) -> str:
    rel = file_path.relative_to(bag_dir).as_posix()
    return f"{storage_root}{rel}"


def verify_uploaded_object(
    client,
    *,
    bucket: str,
    key: str,
    size: int,
    local_digest: str | None,
    local_hashes: FileHashes | None = None,
) -> None:
    """
    Prove the stored object equals the local file.

    HEAD: size, plus the S3 ETag against local_hashes (MD5 / multipart MD5). That covers
    multi-GB disc images without re-downloading them (previously size-only above 256 MiB).
    Files up to VERIFY_DOWNLOAD_MAX_BYTES are additionally downloaded and SHA-256 checked.
    """
    if local_digest is None and local_hashes is None:
        return
    head = client.head_object(Bucket=bucket, Key=key)
    remote_size = int(head["ContentLength"])
    if remote_size != size:
        raise RuntimeError(
            f"Size mismatch after upload: s3://{bucket}/{key} ({remote_size} != {size})"
        )
    if local_hashes is not None:
        ok, detail = etag_matches(local_hashes, str(head.get("ETag") or ""))
        if not ok:
            raise RuntimeError(f"ETag check failed after upload: s3://{bucket}/{key} ({detail})")
    if local_digest is None or size > VERIFY_DOWNLOAD_MAX_BYTES:
        return
    resp = client.get_object(Bucket=bucket, Key=key)
    remote = resp["Body"].read()
    if len(remote) != size:
        raise RuntimeError(f"Size mismatch after upload: s3://{bucket}/{key}")
    if sha256_bytes(remote) != local_digest:
        raise RuntimeError(f"SHA-256 mismatch after upload: s3://{bucket}/{key}")


def list_prefix_keys(client, *, bucket: str, root: str) -> set[str]:
    keys: set[str] = set()
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=root):
        for obj in page.get("Contents") or []:
            keys.add(str(obj["Key"]))
    return keys


def upload_file_to_s3(client, *, bucket: str, key: str, path: Path) -> None:
    # Always use transfer manager (multipart above multipart_threshold). Never PutObject
    # for bag payloads — DVD ISOs exceed the 5 GiB single-put limit on S3/Wasabi.
    client.upload_file(str(path), bucket, key, Config=S3_TRANSFER)


def make_s3_client(wasabi: dict, creds: dict):
    return boto3.client(
        "s3",
        endpoint_url=str(wasabi["endpoint"]).rstrip("/"),
        region_name=wasabi_s3_region(wasabi),
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


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def resolve_storage_prefix(game_key: str, ingest_config_path: Path) -> tuple[str, str, str]:
    """Return (package_id, ingest_kind, package_storage_prefix) from ledger."""
    ingest = load_yaml(ingest_config_path)
    db_path = Path(ingest["database"]["config_file"])
    db_cfg = load_yaml(db_path)["postgres"]
    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, sort_letter, ingest_kind, source_path, package_storage_prefix
                FROM game_ingest WHERE game_key = %s
                """,
                (game_key,),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError(f"No game_ingest row for game_key={game_key!r}")
    package_id = str(row[0] or game_key)
    sort_letter = str(row[1] or "X")
    ingest_kind = str(row[2] or "").strip()
    source_path = str(row[3] or "").strip()
    existing_prefix = str(row[4] or "").strip()
    if not ingest_kind:
        raise RuntimeError(f"game_ingest.ingest_kind is required for Wasabi upload: {game_key!r}")

    if existing_prefix:
        return package_id, ingest_kind, existing_prefix.strip("/")

    if ingest_kind == "dump":
        if not source_path:
            raise RuntimeError(f"game_ingest.source_path required for dump prefix: {game_key!r}")
        clf = classify_dump_sip(Path(source_path), package_id=package_id)
        return package_id, ingest_kind, clf.package_storage_prefix.strip("/")

    if not source_path:
        raise RuntimeError(f"game_ingest.source_path required to build Wasabi prefix: {game_key!r}")
    rel = relative_sip_path(
        Path(source_path),
        material_root_for_kind(ingest_kind, ingest.get("paths") or {}),
    )
    prefix = wasabi_package_storage_prefix(
        ingest_kind,
        package_id=package_id,
        sort_letter=sort_letter,
        relative_sip_path=rel,
    )
    return package_id, ingest_kind, prefix.strip("/")


def upload_bag_to_prefix(
    bag_dir: Path,
    package_storage_prefix: str,
    *,
    ingest_config_path: Path = DEFAULT_INGEST_CONFIG,
    bucket: str | None = None,
    ingest_kind: str | None = None,
) -> dict[str, str]:
    bag_dir = bag_dir.resolve()
    if not bag_dir.is_dir():
        raise RuntimeError(f"Bag directory not found: {bag_dir}")

    wasabi, _ = load_wasabi_settings(ingest_config_path)
    if not require_bool(wasabi, "enabled", where="ingest.yaml wasabi"):
        raise RuntimeError("Wasabi upload is disabled (wasabi.enabled: false)")

    kind = ingest_kind or "dump"
    bucket = str(bucket or resolve_wasabi_bucket(wasabi, kind))
    root = wasabi_unpacked_prefix(package_storage_prefix)
    verify = require_bool(wasabi, "verify_upload", where="ingest.yaml wasabi")
    creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), ingest_config_path.parent.parent))
    client = make_s3_client(wasabi, creds)

    files = iter_package_files(bag_dir)
    uploaded = 0
    uploaded_keys: set[str] = set()
    for path in files:
        key = object_key(root, bag_dir, path)
        size = path.stat().st_size
        hashes = hash_file(path) if verify else None
        upload_file_to_s3(client, bucket=bucket, key=key, path=path)
        verify_uploaded_object(
            client,
            bucket=bucket,
            key=key,
            size=size,
            local_digest=hashes.sha256 if hashes else None,
            local_hashes=hashes,
        )
        uploaded += 1
        uploaded_keys.add(key)
        print(f"  uploaded s3://{bucket}/{key} ({size} bytes{', verified' if verify else ''})")

    # A re-upload over an existing prefix used to leave objects from an older bag in place,
    # making the Wasabi bag BagIt-invalid. Report them (never delete preservation objects here).
    stale = sorted(list_prefix_keys(client, bucket=bucket, root=root) - uploaded_keys)
    if stale:
        raise RuntimeError(
            f"{len(stale)} object(s) under s3://{bucket}/{root} are not part of this bag "
            f"(left from an earlier upload?): {stale[:10]}. Inspect and remove them "
            "deliberately (e.g. purge-wasabi-prefix.py on this package prefix) before retrying."
        )

    s3_uri = package_storage_s3_uri(bucket, package_storage_prefix)
    print(f"Wasabi bag upload complete: {uploaded} objects under {s3_uri}")
    return {
        "bucket": bucket,
        "package_storage_prefix": package_storage_prefix.strip("/"),
        "s3_uri": s3_uri,
        "object_prefix": root,
    }


def update_ledger_wasabi_upload(
    game_key: str,
    upload: dict[str, str],
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
                    package_storage_prefix = %s,
                    storage_layout = %s,
                    updated_at = NOW()
                WHERE game_key = %s
                """,
                (
                    str(upload["s3_uri"]),
                    now,
                    str(upload["package_storage_prefix"]),
                    "bagit_unpacked",
                    game_key,
                ),
            )
            if cur.rowcount == 0:
                raise RuntimeError(f"No game_ingest row for game_key={game_key!r}")
        conn.commit()


def upload_bag_for_game_key(
    bag_dir: Path,
    game_key: str,
    *,
    ingest_config_path: Path = DEFAULT_INGEST_CONFIG,
) -> dict[str, str]:
    ingest = load_yaml(ingest_config_path)
    wasabi = ingest.get("wasabi") or {}
    _package_id, ingest_kind, storage_prefix = resolve_storage_prefix(game_key, ingest_config_path)
    upload = upload_bag_to_prefix(
        bag_dir,
        storage_prefix,
        ingest_config_path=ingest_config_path,
        bucket=resolve_wasabi_bucket(wasabi, ingest_kind),
        ingest_kind=ingest_kind,
    )
    update_ledger_wasabi_upload(game_key, upload, ingest_config_path=ingest_config_path)
    return upload


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("Usage: upload-bag-wasabi.py <bag_dir> <game_key> [ingest.yaml]")
    bag_dir = Path(sys.argv[1])
    game_key = sys.argv[2]
    ingest_path = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_INGEST_CONFIG
    try:
        upload_bag_for_game_key(bag_dir, game_key, ingest_config_path=ingest_path)
    except ClientError as e:
        raise SystemExit(f"Wasabi S3 error: {e}") from e


if __name__ == "__main__":
    main()
