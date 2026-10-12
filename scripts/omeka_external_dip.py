"""Attach Omeka DIP media that read unpacked BagIt packages from Wasabi (S3 API)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import requests

from package_ledger import ledger_bagit_wasabi_paths


def resolve_bagit_external_dip(
    game_key: str,
    ingest_cfg: dict,
) -> dict[str, str | int]:
    db_path = Path(ingest_cfg["database"]["config_file"])
    import yaml

    db_cfg = yaml.safe_load(db_path.read_text())["postgres"]
    wasabi = ingest_cfg.get("wasabi") or {}
    s3_uri, prefix = ledger_bagit_wasabi_paths(db_cfg, game_key, wasabi)
    return {
        "s3_uri": s3_uri,
        "package_storage_prefix": prefix,
        "dip_storage": "wasabi-s3",
    }


def attach_external_dip_media(
    session: requests.Session,
    base: str,
    creds: dict,
    *,
    item_id: int,
    s3_uri: str,
    ingester: str,
    is_public: bool,
    package_bytes: int = 0,
    sha256: str = "",
    package_storage_prefix: str = "",
    dip_storage: str = "wasabi-s3",
    api_url: Callable[[str, str, dict], str],
    merge_media_data_json: Callable[[int, dict[str, Any]], None] | None = None,
) -> int:
    """POST omeka_dip_package media row (Wasabi prefix only; no Omeka stored file)."""
    data: dict[str, Any] = {
        "dip_storage": dip_storage,
        "dip_wasabi_uri": s3_uri,
        "package_wasabi_uri": s3_uri,
        "dip_size_bytes": int(package_bytes),
        "package_storage_prefix": package_storage_prefix,
    }
    if sha256:
        data["dip_sha256"] = sha256

    payload: dict[str, Any] = {
        "o:ingester": ingester,
        "o:item": {"o:id": item_id},
        "o:is_public": is_public,
        "data": data,
    }

    url = api_url(base, "media", creds)
    resp = session.post(
        url,
        json=payload,
        headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"External DIP media create failed ({resp.status_code}): {resp.text[:2000]}")
    body = resp.json()
    media_id = int(body["o:id"])
    response_data = body.get("data") if isinstance(body.get("data"), dict) else {}
    ok = str(response_data.get("package_storage_prefix") or "") == package_storage_prefix
    if not ok and merge_media_data_json:
        merge_media_data_json(media_id, data)
    return media_id


def create_item_without_media(
    session: requests.Session,
    base: str,
    creds: dict,
    *,
    title: str,
    site_id: int,
    item_set_id: int,
    is_public: bool,
    title_property_id: int,
    extra_values: dict[str, list] | None,
    literal_value: Callable[[int, str], dict],
    api_url: Callable[[str, str, dict], str],
) -> int:
    payload: dict[str, Any] = {
        "dcterms:title": [literal_value(title_property_id, title)],
        "o:is_public": is_public,
        "o:site": [{"o:id": site_id}],
        "o:item_set": [{"o:id": item_set_id}],
    }
    if extra_values:
        payload.update(extra_values)
    url = api_url(base, "items", creds)
    resp = session.post(
        url,
        json=payload,
        headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"Create item failed ({resp.status_code}): {resp.text[:2000]}")
    return int(resp.json()["o:id"])
