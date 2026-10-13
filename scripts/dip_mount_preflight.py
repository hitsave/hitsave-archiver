"""Preflight BagIt DIP availability on Wasabi before Omeka item create."""
from __future__ import annotations

from pathlib import Path

try:
    import boto3
    import yaml
    from botocore.config import Config
except ImportError:
    boto3 = None  # type: ignore[assignment]


class DipNotAvailableError(RuntimeError):
    """Raised when the unpacked bag prefix is missing on Wasabi."""


def _load_wasabi_creds(config_root: Path, ingest_cfg: dict) -> dict:
    rel = Path(str(ingest_cfg["wasabi"]["credentials_file"]))
    path = config_root / rel
    if not path.is_file() and rel.parts and rel.parts[0] == "secrets":
        path = config_root / "secrets" / rel.name
    if not path.is_file():
        raise FileNotFoundError(f"Wasabi credentials not found: {rel}")
    return yaml.safe_load(path.read_text())


def head_wasabi_bagit_txt(
    ingest_cfg: dict,
    package_storage_prefix: str,
    *,
    config_root: Path,
    bucket: str | None = None,
    ingest_kind: str | None = None,
) -> int:
    """Return bagit.txt size; raise DipNotAvailableError if prefix is missing."""
    if boto3 is None:
        raise RuntimeError("boto3 required for Wasabi preflight")
    from preservation_common import wasabi_bucket_for_storage_prefix

    key = f"{package_storage_prefix.strip('/')}/bagit.txt"
    wasabi = ingest_cfg["wasabi"]
    target_bucket = bucket or wasabi_bucket_for_storage_prefix(
        wasabi, package_storage_prefix, ingest_kind=ingest_kind
    )
    creds = _load_wasabi_creds(config_root, ingest_cfg)
    client = boto3.client(
        "s3",
        endpoint_url=wasabi["endpoint"],
        region_name=wasabi["region"],
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        config=Config(signature_version="s3v4"),
    )
    try:
        head = client.head_object(Bucket=target_bucket, Key=key)
    except Exception as e:
        from botocore.exceptions import ClientError

        if not isinstance(e, ClientError):
            raise
        code = e.response.get("Error", {}).get("Code", "")
        raise DipNotAvailableError(
            f"Wasabi bag missing bagit.txt for prefix {package_storage_prefix!r}: {code}"
        ) from e
    return int(head["ContentLength"])


def assert_bagit_external_dip_available(
    *,
    ingest_cfg: dict,
    catalog_cfg: dict | None = None,
    config_root: Path,
    package_storage_prefix: str = "",
    expected_bytes: int = 0,
    ingest_kind: str | None = None,
) -> int:
    """Verify unpacked BagIt prefix exists on Wasabi."""
    _ = catalog_cfg
    prefix = str(package_storage_prefix).strip("/")
    if not prefix:
        raise DipNotAvailableError("package_storage_prefix is required for BagIt Omeka attach")
    size = head_wasabi_bagit_txt(
        ingest_cfg,
        prefix,
        config_root=config_root,
        ingest_kind=ingest_kind,
    )
    if expected_bytes > 0 and size <= 0:
        raise DipNotAvailableError(f"Wasabi bagit.txt unreadable for prefix {prefix!r}")
    return size
