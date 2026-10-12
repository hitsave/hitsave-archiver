"""Staff-only Omeka item metadata (preservation URIs, not shown to anonymous users)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import requests

try:
    import psycopg
    import yaml
except ImportError:
    psycopg = None  # type: ignore[assignment,misc]
    yaml = None  # type: ignore[assignment,misc]


class OmekaItemApi(Protocol):
    @staticmethod
    def api_url(base: str, path: str, creds: dict) -> str: ...

    @staticmethod
    def resolve_property_id(
        session: requests.Session, base: str, creds: dict, term: str
    ) -> int: ...

    @staticmethod
    def patch_item_rdf_values(
        session: requests.Session,
        base: str,
        creds: dict,
        item_id: int,
        updates: dict[str, list],
        *,
        item_body: dict | None = None,
    ) -> dict[str, list]: ...


def aip_s3_uri(*, bucket: str, storage_prefix: str) -> str:
    """Full s3 URI for an unpacked AIP tree (ledger aip_wasabi_prefix)."""
    bucket = bucket.strip()
    prefix = storage_prefix.strip().strip("/")
    if not bucket or not prefix:
        raise ValueError("bucket and storage_prefix required")
    return f"s3://{bucket}/{prefix}/"


def uri_value_row(property_id: int, uri: str, *, is_public: bool) -> dict[str, Any]:
    return {
        "property_id": property_id,
        "type": "uri",
        "@id": uri,
        "is_public": is_public,
    }


def item_has_uri(item_body: dict, term: str, uri: str) -> bool:
    for row in item_body.get(term) or []:
        if str(row.get("@id") or "") == uri:
            return True
    return False


def merge_uri_property(
    item_body: dict,
    term: str,
    new_row: dict[str, Any],
) -> dict[str, list] | None:
    uri = str(new_row.get("@id") or "")
    if not uri:
        return None
    if item_has_uri(item_body, term, uri):
        return None
    merged = [dict(row) for row in (item_body.get(term) or [])]
    merged.append(new_row)
    return {term: merged}


def _patch_item_aip_uri(
    session: requests.Session,
    base: str,
    creds: dict,
    item_id: int,
    *,
    aip_uri: str,
    uri_property: str,
    uri_is_public: bool,
    api,
) -> bool:
    get_url = api.api_url(base, f"items/{item_id}", creds)
    resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"GET item {item_id} failed ({resp.status_code}): {resp.text[:800]}")
    item_body = resp.json()
    pid = api.resolve_property_id(session, base, creds, uri_property)
    row = uri_value_row(pid, aip_uri, is_public=uri_is_public)
    updates = merge_uri_property(item_body, uri_property, row)
    if not updates:
        return False
    api.patch_item_rdf_values(session, base, creds, item_id, updates, item_body=item_body)
    return True


def _load_yaml(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML required")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def ledger_aip_wasabi_prefix(db_cfg: dict, game_key: str) -> str | None:
    if psycopg is None:
        raise RuntimeError("psycopg required")
    conn = psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT aip_wasabi_prefix FROM game_ingest WHERE game_key = %s",
                (game_key,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    return str(row[0]).strip()


def apply_item_aip_uri(
    session: requests.Session,
    base: str,
    creds: dict,
    game_key: str,
    item_id: int,
    *,
    ingest_cfg: dict,
    config_root: Path,
    api: type[OmekaItemApi],
) -> str | None:
    """Apply configured staff-only s3:// AIP URI from the ledger onto an Omeka item."""
    uploader_cfg = _load_yaml(config_root / "omeka-uploader.yaml")
    aip_cfg = uploader_cfg.get("aip_wasabi") or {}
    if not aip_cfg.get("apply_uri_after_upload", False):
        return None
    wasabi = ingest_cfg.get("wasabi") or {}
    bucket = str(wasabi.get("bucket") or "").strip()
    if not bucket:
        return None
    db_cfg = _load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    prefix = ledger_aip_wasabi_prefix(db_cfg, game_key)
    if not prefix:
        return None
    uri = aip_s3_uri(bucket=bucket, storage_prefix=prefix)
    term = str(aip_cfg.get("uri_property") or "dcterms:relation").strip()
    is_public = bool(aip_cfg.get("uri_is_public", False))
    _patch_item_aip_uri(
        session,
        base,
        creds,
        item_id,
        aip_uri=uri,
        uri_property=term,
        uri_is_public=is_public,
        api=api,
    )
    return uri
