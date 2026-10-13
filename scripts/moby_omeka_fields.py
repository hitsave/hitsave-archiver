#!/usr/bin/env python3
"""Map ledger Moby metadata to Omeka API property payloads (shared by upload + apply)."""
from __future__ import annotations

from typing import Any, Callable

from moby_resolve import (
    moby_attribution_for_omeka_rights,
    moby_description_for_omeka,
    normalize_moby_omeka_profile,
    uses_moby_catalog_data,
)
from omeka_staff_metadata import is_wasabi_s3_uri
from press_material_description import press_material_description


def moby_bibo_uri_rows(
    item_body: dict | None,
    moby_url: str,
    property_id: int,
    uri_value: Callable[[int, str], dict],
) -> list[dict]:
    """Public Moby game page in bibo:uri (never mix with Wasabi s3:// storage URIs)."""
    rows: list[dict] = []
    seen: set[str] = set()
    if item_body:
        for row in item_body.get("bibo:uri") or []:
            uri = str(row.get("@id") or "").strip()
            if not uri or is_wasabi_s3_uri(uri):
                continue
            if uri in seen:
                continue
            seen.add(uri)
            rows.append(dict(row))
    url = moby_url.strip()
    if url and url not in seen:
        row = uri_value(property_id, url)
        row.setdefault("is_public", True)
        rows.append(row)
    return rows


def append_moby_fields_to_payload(
    payload: dict[str, list],
    metadata_json: dict,
    moby_cfg: dict,
    *,
    session: Any,
    base: str,
    creds: dict,
    literal_value: Callable[[int, str], dict],
    uri_value: Callable[[int, str], dict],
    resolve_property_id: Callable[..., int],
    game_title: str | None = None,
    inventory_json: dict | None = None,
    item_body: dict | None = None,
    omeka_profile: str = "press_material",
) -> None:
    if not uses_moby_catalog_data(metadata_json):
        return
    profile = normalize_moby_omeka_profile(omeka_profile)
    if profile == "none":
        return
    credit = moby_attribution_for_omeka_rights(
        metadata_json, moby_cfg, omeka_profile=profile
    )
    payload["dcterms:rights"] = [
        literal_value(resolve_property_id(session, base, creds, "dcterms:rights"), credit)
    ]
    title = (game_title or metadata_json.get("moby_title") or metadata_json.get("search_title") or "").strip()
    if profile == "game_catalog":
        desc = moby_description_for_omeka(metadata_json, moby_cfg)
    else:
        desc = press_material_description(title, inventory_json)
    payload["dcterms:description"] = [
        literal_value(resolve_property_id(session, base, creds, "dcterms:description"), desc)
    ]
    moby_url = metadata_json.get("moby_url")
    if not moby_url and metadata_json.get("moby_game_id"):
        moby_url = f"https://www.mobygames.com/game/{metadata_json['moby_game_id']}"
    if moby_url:
        bibo_pid = resolve_property_id(session, base, creds, "bibo:uri")
        rows = moby_bibo_uri_rows(item_body, str(moby_url), bibo_pid, uri_value)
        if rows:
            payload["bibo:uri"] = rows
    if profile == "game_catalog":
        release = metadata_json.get("first_release_date")
        if release:
            payload["dcterms:date"] = [
                literal_value(resolve_property_id(session, base, creds, "dcterms:date"), release)
            ]
    else:
        # Omeka PATCH merges existing RDF values; clear stale Moby release dates explicitly.
        payload["dcterms:date"] = []
    publishers = metadata_json.get("publishers") or []
    if publishers:
        publisher_pid = resolve_property_id(session, base, creds, "dcterms:publisher")
        payload["dcterms:publisher"] = [literal_value(publisher_pid, name) for name in publishers]
    developers = metadata_json.get("developers") or []
    if developers:
        creator_pid = resolve_property_id(session, base, creds, "dcterms:creator")
        payload["dcterms:creator"] = [literal_value(creator_pid, name) for name in developers]
