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


def is_wasabi_s3_uri(uri: str) -> bool:
    return str(uri or "").strip().lower().startswith("s3://")


def filter_bibo_uri_public(item_body: dict) -> dict[str, list] | None:
    """Drop Wasabi s3:// values from bibo:uri (Moby game pages use this property)."""
    term = "bibo:uri"
    rows = item_body.get(term) or []
    kept = [dict(row) for row in rows if not is_wasabi_s3_uri(str(row.get("@id") or ""))]
    if len(kept) == len(rows):
        return None
    return {term: kept}


def merge_staff_preservation_uri(
    item_body: dict,
    term: str,
    new_row: dict[str, Any],
) -> dict[str, list]:
    """Keep non-s3 relation/URI values; store exactly one staff s3:// row (private)."""
    uri = str(new_row.get("@id") or "")
    rows = [dict(row) for row in (item_body.get(term) or [])]
    rows = [row for row in rows if not is_wasabi_s3_uri(str(row.get("@id") or ""))]
    if uri:
        rows.append(new_row)
    return {term: rows}


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
    bibo_fix = filter_bibo_uri_public(item_body)
    s3_rows = [
        row
        for row in (item_body.get(uri_property) or [])
        if is_wasabi_s3_uri(str(row.get("@id") or ""))
    ]
    relation_ok = (
        len(s3_rows) == 1
        and str(s3_rows[0].get("@id") or "") == aip_uri
        and bool(s3_rows[0].get("is_public")) == uri_is_public
    )
    if relation_ok and not bibo_fix:
        return False
    updates: dict[str, list] = merge_staff_preservation_uri(item_body, uri_property, row)
    if bibo_fix:
        updates = {**updates, **bibo_fix}
    api.patch_item_rdf_values(session, base, creds, item_id, updates, item_body=item_body)
    return True


def _load_yaml(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML required")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_omeka_catalog_config(config_root: Path) -> dict:
    """Omeka REST registration settings (omeka-catalog.yaml; omeka-uploader.yaml is deprecated)."""
    for name in ("omeka-catalog.yaml", "omeka-uploader.yaml"):
        path = config_root / name
        if path.is_file():
            return _load_yaml(path)
    raise FileNotFoundError(f"Missing omeka-catalog.yaml under {config_root}")


def apply_staff_aip_uri_after_register(aip_cfg: dict) -> bool:
    return bool(
        aip_cfg.get("apply_uri_after_register", aip_cfg.get("apply_uri_after_upload", False))
    )


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


def ledger_preservation_package_s3_uri(db_cfg: dict, game_key: str, bucket: str) -> str | None:
    """BagIt s3:// URI from ledger (package_storage_prefix or legacy keys)."""
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
                """
                SELECT package_storage_prefix, aip_wasabi_prefix, package_tar_key
                FROM game_ingest WHERE game_key = %s
                """,
                (game_key,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    storage_prefix, aip_prefix, tar_key = row[0], row[1], row[2]
    if storage_prefix:
        raw = str(storage_prefix).strip()
        if raw.startswith("s3://"):
            return raw.rstrip("/") + "/"
        return aip_s3_uri(bucket=bucket, storage_prefix=raw)
    if tar_key:
        legacy = str(tar_key).lstrip("/")
        if legacy.lower().endswith(".tar"):
            legacy = legacy[:-4]
        return aip_s3_uri(bucket=bucket, storage_prefix=legacy)
    if aip_prefix:
        prefix = aip_prefix
        raw = str(prefix).strip()
        if raw.startswith("s3://"):
            return raw.rstrip("/")
        return aip_s3_uri(bucket=bucket, storage_prefix=raw)
    return None


def strip_wasabi_s3_from_relation(
    session: requests.Session,
    base: str,
    creds: dict,
    item_id: int,
    *,
    relation_term: str = "dcterms:relation",
    api: type[OmekaItemApi],
) -> bool:
    """Remove all s3:// values from relation (e.g. duplicate staff URIs shown when logged in)."""
    get_url = api.api_url(base, f"items/{item_id}", creds)
    resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"GET item {item_id} failed ({resp.status_code}): {resp.text[:800]}")
    item_body = resp.json()
    rows = item_body.get(relation_term) or []
    kept = [dict(row) for row in rows if not is_wasabi_s3_uri(str(row.get("@id") or ""))]
    if len(kept) == len(rows):
        return False
    api.patch_item_rdf_values(
        session, base, creds, item_id, {relation_term: kept}, item_body=item_body
    )
    return True


def strip_public_wasabi_bibo_uri(
    session: requests.Session,
    base: str,
    creds: dict,
    item_id: int,
    *,
    api: type[OmekaItemApi],
) -> bool:
    """Remove s3:// from bibo:uri so anonymous users never see storage locations."""
    get_url = api.api_url(base, f"items/{item_id}", creds)
    resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"GET item {item_id} failed ({resp.status_code}): {resp.text[:800]}")
    item_body = resp.json()
    bibo_fix = filter_bibo_uri_public(item_body)
    if not bibo_fix:
        return False
    api.patch_item_rdf_values(session, base, creds, item_id, bibo_fix, item_body=item_body)
    return True


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
    catalog_cfg = load_omeka_catalog_config(config_root)
    aip_cfg = catalog_cfg.get("aip_wasabi") or {}
    if not apply_staff_aip_uri_after_register(aip_cfg):
        return None
    wasabi = ingest_cfg.get("wasabi") or {}
    bucket = str(wasabi.get("bucket") or "").strip()
    if not bucket:
        return None
    db_cfg = _load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    uri = ledger_preservation_package_s3_uri(db_cfg, game_key, bucket)
    if not uri:
        return None
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
