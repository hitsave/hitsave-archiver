#!/usr/bin/env python3
"""Copy a small set of items (and file media) from production Omeka into omeka-test."""
from __future__ import annotations

import json
import mimetypes
import sys
from pathlib import Path
from urllib.parse import urlencode

try:
    import requests
    import yaml
except ImportError as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config/omeka-test/sample-from-prod.yaml"

SKIP_ITEM_KEYS = {
    "@context",
    "@id",
    "@type",
    "o:id",
    "o:owner",
    "o:created",
    "o:modified",
    "o:thumbnail",
    "o:primary_media",
    "o:media",
    "o:item_set",
    "o:site",
    "o:title",
}


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def creds_query(creds: dict) -> dict:
    return {
        "key_identity": creds["key_identity"],
        "key_credential": creds["key_credential"],
    }


def api_url(base: str, path: str, creds: dict | None) -> str:
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    if not creds:
        return url
    q = urlencode(creds_query(creds))
    return f"{url}?{q}"


def get_json(
    session: requests.Session,
    base: str,
    path: str,
    creds: dict | None,
    *,
    params: dict | None = None,
) -> dict | list:
    merged = dict(params or {})
    if creds:
        merged.update(creds_query(creds))
    resp = session.get(f"{base.rstrip('/')}/{path.lstrip('/')}", params=merged, timeout=120)
    resp.raise_for_status()
    return resp.json()


def resolve_site_id(session: requests.Session, base: str, creds: dict, slug: str) -> int:
    rows = get_json(session, base, "sites", creds, params={"slug": slug})
    if not rows:
        raise RuntimeError(f"No site with slug {slug!r}")
    return int(rows[0]["o:id"])


def resolve_property_id(session: requests.Session, base: str, creds: dict, term: str) -> int:
    rows = get_json(session, base, "properties", creds, params={"term": term})
    if not rows:
        raise RuntimeError(f"Property not found on test site: {term}")
    return int(rows[0]["o:id"])


def resolve_resource_class_id(session: requests.Session, base: str, creds: dict, term: str) -> int:
    rows = get_json(session, base, "resource_classes", creds, params={"term": term})
    if not rows:
        raise RuntimeError(f"Resource class not found on test site: {term}")
    return int(rows[0]["o:id"])


def ensure_item_set(session: requests.Session, base: str, creds: dict, title: str) -> int:
    rows = get_json(session, base, "item_sets", creds, params={"title": title})
    for row in rows:
        if (row.get("o:title") or "").strip() == title.strip():
            return int(row["o:id"])
    url = api_url(base, "item_sets", creds)
    payload = {
        "o:title": title,
        "o:is_public": True,
        "dcterms:title": [
            {
                "type": "literal",
                "property_id": resolve_property_id(session, base, creds, "dcterms:title"),
                "@value": title,
            }
        ],
    }
    resp = session.post(url, json=payload, headers={"Accept": "application/ld+json"}, timeout=120)
    resp.raise_for_status()
    return int(resp.json()["o:id"])


class TermCache:
    def __init__(self) -> None:
        self.prod_terms: dict[int, str] = {}
        self.local_pids: dict[str, int] = {}

    def prod_term(
        self, session: requests.Session, prod_base: str, prod_creds: dict | None, property_id: int
    ) -> str:
        if property_id in self.prod_terms:
            return self.prod_terms[property_id]
        row = get_json(session, prod_base, f"properties/{property_id}", prod_creds)
        term = str(row["o:term"])
        self.prod_terms[property_id] = term
        return term

    def local_pid(
        self, session: requests.Session, local_base: str, local_creds: dict, term: str
    ) -> int:
        if term in self.local_pids:
            return self.local_pids[term]
        pid = resolve_property_id(session, local_base, local_creds, term)
        self.local_pids[term] = pid
        return pid


def remap_property_values(
    item: dict,
    cache: TermCache,
    session: requests.Session,
    prod_base: str,
    prod_creds: dict | None,
    local_base: str,
    local_creds: dict,
) -> dict[str, list]:
    out: dict[str, list] = {}
    for key, rows in item.items():
        if not isinstance(rows, list) or ":" not in key or key.startswith("@"):
            continue
        if not rows or not isinstance(rows[0], dict):
            continue
        mapped: list[dict] = []
        for row in rows:
            pid = row.get("property_id")
            if pid is None:
                continue
            term = cache.prod_term(session, prod_base, prod_creds, int(pid))
            local_pid = cache.local_pid(session, local_base, local_creds, term)
            value_type = row.get("type") or "literal"
            if str(value_type).startswith("customvocab"):
                value_type = "literal"
            entry: dict = {
                "type": value_type,
                "property_id": local_pid,
                "@value": row.get("@value"),
            }
            if row.get("@type") and value_type != "literal":
                entry["@type"] = row["@type"]
            if entry["@value"] is None:
                continue
            mapped.append(entry)
        if mapped:
            out[key] = mapped
    return out


def download_media_file(session: requests.Session, media: dict) -> tuple[str, bytes, str]:
    url = media.get("o:original_url") or media.get("@id")
    if not url:
        raise RuntimeError("Media has no original URL")
    resp = session.get(url, timeout=600)
    resp.raise_for_status()
    filename = media.get("o:filename") or "original.bin"
    mime = media.get("o:media_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return filename, resp.content, mime


def find_item_by_identifier(
    session: requests.Session,
    local_base: str,
    local_creds: dict,
    identifier: str,
    identifier_property_id: int,
) -> int | None:
    rows = get_json(
        session,
        local_base,
        "items",
        local_creds,
        params={
            "property": [
                {
                    "joiner": "and",
                    "property": identifier_property_id,
                    "text": identifier,
                }
            ]
        },
    )
    for row in rows:
        for val in row.get("dcterms:identifier") or []:
            if val.get("@value") == identifier:
                return int(row["o:id"])
    return None


def copy_item(
    session: requests.Session,
    prod_base: str,
    prod_creds: dict | None,
    local_base: str,
    local_creds: dict,
    cache: TermCache,
    *,
    prod_item_id: int,
    site_id: int,
    item_set_id: int,
    resource_class_id: int,
    is_public: bool,
    identifier_property_id: int,
) -> int:
    item = get_json(session, prod_base, f"items/{prod_item_id}", prod_creds)
    values = remap_property_values(item, cache, session, prod_base, prod_creds, local_base, local_creds)
    identifiers = values.get("dcterms:identifier") or []
    if identifiers:
        ident = str(identifiers[0].get("@value", ""))
        if ident:
            existing_id = find_item_by_identifier(
                session, local_base, local_creds, ident, identifier_property_id
            )
            if existing_id is not None:
                return existing_id

    media_refs = item.get("o:media") or []
    if not media_refs:
        raise RuntimeError(f"Prod item {prod_item_id} has no media")
    prod_media = get_json(session, prod_base, f"media/{media_refs[0]['o:id']}", prod_creds)
    filename, file_bytes, mime = download_media_file(session, prod_media)

    payload: dict = {
        "o:is_public": is_public,
        "o:resource_class": {"o:id": resource_class_id},
        "o:site": [{"o:id": site_id}],
        "o:item_set": [{"o:id": item_set_id}],
        "o:media": [
            {
                "o:ingester": "upload",
                "file_index": 0,
                "o:is_public": is_public,
            }
        ],
    }
    payload.update(values)

    url = api_url(local_base, "items", local_creds)
    resp = session.post(
        url,
        headers={"Accept": "application/ld+json"},
        data={"data": json.dumps(payload)},
        files={"file[0]": (filename, file_bytes, mime)},
        timeout=600,
    )
    if not resp.ok:
        raise RuntimeError(f"Local item create failed ({resp.status_code}): {resp.text[:2000]}")
    return int(resp.json()["o:id"])


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    cfg = load_yaml(config_path)
    prod_cfg = cfg["production"]
    prod_base = prod_cfg["base_url"].rstrip("/")
    prod_creds_path = REPO_ROOT / prod_cfg.get("credentials_file", "")
    prod_creds = None
    if prod_creds_path.is_file():
        prod_creds = load_yaml(prod_creds_path)
        if prod_creds.get("key_identity") in (None, "", "REPLACE_ME"):
            prod_creds = None

    local_api = load_yaml(REPO_ROOT / cfg["local"]["api_config"])["api"]
    local_base = local_api["base_url"].rstrip("/")
    local_creds_path = REPO_ROOT / local_api["credentials_file"]
    local_creds = load_yaml(local_creds_path)

    query = cfg.get("query") or {}
    limit = int(query.get("limit", 4))
    search_params = {
        k: query[k]
        for k in ("fulltext_search", "sort_by", "sort_order")
        if k in query and query[k] is not None
    }
    search_params["per_page"] = limit

    session = requests.Session()
    cache = TermCache()

    prod_items = get_json(session, prod_base, "items", prod_creds, params=search_params)
    if not prod_items:
        raise SystemExit("No production items matched the query.")

    site_id = resolve_site_id(session, local_base, local_creds, local_api["site_slug"])
    item_set_id = ensure_item_set(session, local_base, local_creds, cfg["item_set_title"])
    class_id = resolve_resource_class_id(
        session, local_base, local_creds, cfg.get("resource_class_term", "dctype:MovingImage")
    )
    identifier_property_id = resolve_property_id(session, local_base, local_creds, "dcterms:identifier")
    is_public = bool(cfg.get("is_public", True))

    copied: list[dict] = []
    for row in prod_items[:limit]:
        prod_id = int(row["o:id"])
        try:
            local_id = copy_item(
                session,
                prod_base,
                prod_creds,
                local_base,
                local_creds,
                cache,
                prod_item_id=prod_id,
                site_id=site_id,
                item_set_id=item_set_id,
                resource_class_id=class_id,
                is_public=is_public,
                identifier_property_id=identifier_property_id,
            )
            copied.append({"prod_item_id": prod_id, "local_item_id": local_id, "title": row.get("o:title")})
            print(f"Copied prod item {prod_id} → local {local_id}: {row.get('o:title')}")
        except Exception as exc:
            print(f"Skip prod item {prod_id}: {exc}", file=sys.stderr)

    print(json.dumps({"item_set_id": item_set_id, "copied": copied}, indent=2))


if __name__ == "__main__":
    main()
