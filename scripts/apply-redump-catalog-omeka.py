#!/usr/bin/env python3
"""Create a catalog-only Omeka item for a Redump disc dump (no media, no Moby)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    import requests
    import yaml
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import importlib.util  # noqa: E402

CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))


def _upload_api_module():
    path = SCRIPTS / "upload-dip-omeka-api.py"
    spec = importlib.util.spec_from_file_location("upload_dip_omeka_api", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _set_resource_public(
    session: requests.Session,
    base: str,
    creds: dict,
    resource: str,
    resource_id: int,
    *,
    is_public: bool,
    api,
) -> None:
    url = api.api_url(base, f"{resource}/{resource_id}", creds)
    resp = session.patch(
        url,
        headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
        json={"o:is_public": is_public},
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(
            f"PATCH {resource} {resource_id} visibility failed ({resp.status_code}): {resp.text[:800]}"
        )


def ensure_item_set(
    session: requests.Session,
    base: str,
    creds: dict,
    title: str,
    *,
    is_public: bool,
    api,
) -> int:
    try:
        item_set_id = api.resolve_item_set_id(session, base, creds, title)
    except RuntimeError:
        url = api.api_url(base, "item_sets", creds)
        payload = {"o:title": title, "o:is_public": is_public}
        resp = session.post(url, json=payload, headers={"Accept": "application/ld+json"}, timeout=120)
        if not resp.ok:
            raise RuntimeError(f"Create item set failed ({resp.status_code}): {resp.text[:800]}")
        item_set_id = int(resp.json()["o:id"])
    else:
        _set_resource_public(
            session, base, creds, "item_sets", item_set_id, is_public=is_public, api=api
        )
    return item_set_id


def _append_literal(
    out: dict[str, list],
    session: requests.Session,
    base: str,
    creds: dict,
    term: str,
    value: str | None,
    *,
    api,
) -> None:
    if not value or not str(value).strip():
        return
    pid = api.resolve_property_id(session, base, creds, term)
    out.setdefault(term, []).append(api.literal_value(pid, str(value).strip()))


def _redump_field(fields: dict[str, str], key: str) -> str | None:
    raw = (fields.get(key) or "").strip()
    if not raw or raw.startswith("(OPTIONAL)") or raw.startswith("(REQUIRED"):
        return None
    return raw


def _catalog_alternates(title: str, foreign: str | None, alternates: list[str]) -> list[str]:
    """Foreign title plus any [T:ALT] titles, each distinct from the main title and each other."""
    main = (title or "").strip()
    seen = {main.casefold()} if main else set()
    out: list[str] = []
    for raw in [foreign, *alternates]:
        val = (raw or "").strip()
        if not val:
            continue
        key = val.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(val)
    return out


def build_redump_values(
    session: requests.Session,
    base: str,
    creds: dict,
    manifest: dict[str, Any],
    *,
    storage_uri_base: str,
    api,
    redump_cfg: dict[str, Any] | None = None,
    existing_item: dict | None = None,
) -> tuple[dict[str, list], str]:
    """
    Catalog fields for Omeka RDF terms.

    Title and alternative titles come from redump.info when SHA-1 resolves a disc;
    other fields still use submissionInfo / manifest. Full sidecar stays in Wasabi.
    """
    fields = (manifest.get("redump") or {}).get("parsed", {}).get("fields") or {}
    alternates = (manifest.get("redump") or {}).get("parsed", {}).get("alternate_titles") or []
    wasabi = manifest.get("wasabi") or {}
    fallback_title = str(manifest.get("omeka_item_title") or fields.get("Title") or "")

    from redump_info_lookup import redump_disc_page_url, resolve_redump_disc_by_sha1

    cfg = redump_cfg or {}
    checksums = (manifest.get("redump") or {}).get("parsed", {}).get("checksums") or {}
    sha1 = checksums.get("sha1") or fields.get("SHA1")
    resolved = resolve_redump_disc_by_sha1(session, sha1=str(sha1) if sha1 else None, redump_cfg=cfg)

    description: str | None = None
    if resolved is not None:
        disc_id, redump_titles = resolved
        title = redump_titles.title
        foreign = redump_titles.foreign_title
        extra_alts = list(redump_titles.alternative_titles)
        description = redump_titles.contents
    else:
        disc_id = None
        title = fallback_title
        foreign = fields.get("Foreign Title (Non-latin)")
        extra_alts = alternates

    values: dict[str, list] = {}
    _append_literal(values, session, base, creds, "dcterms:title", title, api=api)
    for alt in _catalog_alternates(title, foreign, extra_alts):
        _append_literal(values, session, base, creds, "dcterms:alternative", alt, api=api)

    serial = _redump_field(fields, "Disc Serial") or _redump_field(fields, "Barcode")
    if serial:
        id_rows: list[dict] = []
        if existing_item is not None:
            for row in existing_item.get("dcterms:identifier") or []:
                val = str(row.get("@value") or "")
                if val.startswith("ark:"):
                    id_rows.append(row)
        pid = api.resolve_property_id(session, base, creds, "dcterms:identifier")
        id_rows.append(api.literal_value(pid, serial))
        values["dcterms:identifier"] = id_rows

    # Redump → DC (one value per property unless noted).
    for fmt in (_redump_field(fields, "System"), _redump_field(fields, "Media Type")):
        _append_literal(values, session, base, creds, "dcterms:format", fmt, api=api)
    _append_literal(values, session, base, creds, "dcterms:type", _redump_field(fields, "Category"), api=api)
    _append_literal(values, session, base, creds, "dcterms:coverage", _redump_field(fields, "Region"), api=api)
    _append_literal(values, session, base, creds, "dcterms:language", _redump_field(fields, "Languages"), api=api)

    if wasabi.get("bucket") and wasabi.get("prefix"):
        staff_uri = f"s3://{wasabi['bucket']}/{wasabi['prefix']}"
    else:
        game_key = str(manifest.get("game_key") or "")
        staff_uri = f"{storage_uri_base.rstrip('/')}/{game_key}/"
    bibo_pid = api.resolve_property_id(session, base, creds, "bibo:uri")
    values["bibo:uri"] = [api.uri_value(bibo_pid, staff_uri)]

    if disc_id is not None:
        redump_url = redump_disc_page_url(disc_id, cfg)
        relation_pid = api.resolve_property_id(session, base, creds, "dcterms:relation")
        values["dcterms:relation"] = [api.uri_value(relation_pid, redump_url)]

    if description:
        _append_literal(values, session, base, creds, "dcterms:description", description, api=api)
    else:
        values["dcterms:description"] = []
    values["dcterms:source"] = []
    return values, title


def patch_catalog_item(
    session: requests.Session,
    base: str,
    creds: dict,
    item_id: int,
    manifest: dict[str, Any],
    *,
    storage_uri_base: str,
    api,
    redump_cfg: dict[str, Any] | None = None,
    is_public: bool = True,
) -> str:
    get_url = api.api_url(base, f"items/{item_id}", creds)
    resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"GET item {item_id} failed ({resp.status_code}): {resp.text[:800]}")
    item_body = resp.json()
    updates, catalog_title = build_redump_values(
        session,
        base,
        creds,
        manifest,
        storage_uri_base=storage_uri_base,
        api=api,
        redump_cfg=redump_cfg,
        existing_item=item_body,
    )
    api.patch_item_rdf_values(session, base, creds, item_id, updates, item_body=item_body)
    _set_resource_public(session, base, creds, "items", item_id, is_public=is_public, api=api)
    return catalog_title


def create_catalog_item(
    session: requests.Session,
    base: str,
    creds: dict,
    *,
    site_id: int,
    item_set_id: int,
    is_public: bool,
    rdf_values: dict[str, list],
    api,
) -> int:
    payload: dict[str, Any] = {
        "o:is_public": is_public,
        "o:site": [{"o:id": site_id}],
        "o:item_set": [{"o:id": item_set_id}],
    }
    payload.update(rdf_values)
    url = api.api_url(base, "items", creds)
    resp = session.post(url, json=payload, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"Create catalog item failed ({resp.status_code}): {resp.text[:2000]}")
    body = resp.json()
    return int(body["o:id"])


def main() -> None:
    if len(sys.argv) not in (2, 4) or (len(sys.argv) == 4 and sys.argv[2] != "--item-id"):
        print(
            "Usage: apply-redump-catalog-omeka.py <hitsave-submission.yaml> [--item-id ID]",
            file=sys.stderr,
        )
        sys.exit(1)
    manifest_path = Path(sys.argv[1])
    patch_item_id = int(sys.argv[3]) if len(sys.argv) == 4 else None
    manifest = load_yaml(manifest_path)

    uploader_cfg = load_yaml(CONFIG_ROOT / "omeka-uploader.yaml")
    api_cfg = uploader_cfg.get("api") or {}
    redump_cfg = uploader_cfg.get("redump_catalog") or {}
    cred_path = CONFIG_ROOT / str(api_cfg.get("credentials_file", "secrets/omeka-api-credentials-local.yaml"))
    creds = load_yaml(cred_path)

    base = str(api_cfg["base_url"]).rstrip("/")
    site_slug = str(api_cfg.get("site_slug", "hitsave-test"))
    item_set_title = str(redump_cfg.get("item_set_title", "Redump disc dumps"))
    is_public = bool(redump_cfg.get("is_public", True))
    storage_uri_base = str(
        redump_cfg.get("storage_uri_base", "http://10.1.1.60:8088/staff/redump")
    )

    api = _upload_api_module()
    session = requests.Session()
    site_id = api.resolve_site_id(session, base, creds, site_slug)
    if patch_item_id is not None:
        catalog_title = patch_catalog_item(
            session,
            base,
            creds,
            patch_item_id,
            manifest,
            storage_uri_base=storage_uri_base,
            api=api,
            redump_cfg=redump_cfg,
            is_public=is_public,
        )
        item_id = patch_item_id
    else:
        item_set_id = ensure_item_set(
            session, base, creds, item_set_title, is_public=is_public, api=api
        )
        rdf_values, catalog_title = build_redump_values(
            session,
            base,
            creds,
            manifest,
            storage_uri_base=storage_uri_base,
            api=api,
            redump_cfg=redump_cfg,
        )
        item_id = create_catalog_item(
            session,
            base,
            creds,
            site_id=site_id,
            item_set_id=item_set_id,
            is_public=is_public,
            rdf_values=rdf_values,
            api=api,
        )
    print(
        json.dumps(
            {
                "item_id": item_id,
                "game_key": manifest.get("game_key"),
                "title": catalog_title,
                "site_slug": site_slug,
                "item_set": item_set_title,
                "is_public": is_public,
                "patched": patch_item_id is not None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
