"""Omeka catalog-only items from GDrive/handoff YAML (no ledger Moby path)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from dump_catalog_omeka import build_redump_values, dump_catalog_config
from omeka_catalog_common import (
    ensure_item_set,
    load_register_dip_omeka_api,
    set_resource_public,
)
from omeka_staff_metadata import load_omeka_catalog_config, require_omeka_api_config
from preservation_common import load_ingest_config
from yaml_config import load_yaml


def patch_handoff_catalog_item(
    session: requests.Session,
    base: str,
    creds: dict,
    item_id: int,
    manifest: dict[str, Any],
    *,
    storage_uri_base: str,
    api,
    dump_cfg: dict[str, Any] | None = None,
    wasabi_ingest_cfg: dict | None = None,
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
        redump_cfg=dump_cfg,
        existing_item=item_body,
        wasabi_ingest_cfg=wasabi_ingest_cfg,
    )
    api.patch_item_rdf_values(session, base, creds, item_id, updates, item_body=item_body)
    set_resource_public(session, base, creds, "items", item_id, is_public=is_public, api=api)
    return catalog_title


def create_handoff_catalog_item(
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
    return int(resp.json()["o:id"])


def apply_handoff_dump_catalog(
    manifest: dict[str, Any],
    *,
    config_root: Path,
    patch_item_id: int | None = None,
) -> dict[str, Any]:
    """Create or patch a metadata-only dump catalog item from handoff YAML."""
    uploader_cfg = load_omeka_catalog_config(config_root)
    api_cfg = require_omeka_api_config(uploader_cfg)
    dump_cfg = dump_catalog_config(uploader_cfg)
    creds = load_yaml(config_root / api_cfg["credentials_file"])

    base = api_cfg["base_url"].rstrip("/")
    site_slug = api_cfg["site_slug"]
    item_set_title = dump_cfg["item_set_title"]
    is_public = dump_cfg["is_public"]
    storage_uri_base = dump_cfg["storage_uri_base"]
    wasabi_ingest_cfg = load_ingest_config().get("wasabi")

    api = load_register_dip_omeka_api()
    session = requests.Session()
    site_id = api.resolve_site_id(session, base, creds, site_slug)

    if patch_item_id is not None:
        catalog_title = patch_handoff_catalog_item(
            session,
            base,
            creds,
            patch_item_id,
            manifest,
            storage_uri_base=storage_uri_base,
            api=api,
            dump_cfg=dump_cfg,
            wasabi_ingest_cfg=wasabi_ingest_cfg,
            is_public=is_public,
        )
        item_id = patch_item_id
    else:
        item_set_id = ensure_item_set(
            session,
            base,
            creds,
            item_set_title,
            is_public=is_public,
            api=api,
            sync_visibility_on_existing=True,
        )
        rdf_values, catalog_title = build_redump_values(
            session,
            base,
            creds,
            manifest,
            storage_uri_base=storage_uri_base,
            api=api,
            redump_cfg=dump_cfg,
            wasabi_ingest_cfg=wasabi_ingest_cfg,
        )
        item_id = create_handoff_catalog_item(
            session,
            base,
            creds,
            site_id=site_id,
            item_set_id=item_set_id,
            is_public=is_public,
            rdf_values=rdf_values,
            api=api,
        )

    return {
        "item_id": item_id,
        "game_key": manifest.get("game_key"),
        "title": catalog_title,
        "site_slug": site_slug,
        "item_set": item_set_title,
        "is_public": is_public,
        "patched": patch_item_id is not None,
    }
