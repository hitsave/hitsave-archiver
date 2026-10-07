#!/usr/bin/env python3
"""Map ledger Moby metadata to Omeka API property payloads (shared by upload + apply)."""
from __future__ import annotations

from typing import Any, Callable

from moby_resolve import moby_attribution_text, moby_description_for_omeka, uses_moby_catalog_data


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
) -> None:
    if not uses_moby_catalog_data(metadata_json):
        return
    credit = moby_attribution_text(metadata_json, moby_cfg)
    payload["dcterms:rights"] = [
        literal_value(resolve_property_id(session, base, creds, "dcterms:rights"), credit)
    ]
    desc = moby_description_for_omeka(metadata_json, moby_cfg)
    if desc:
        payload["dcterms:description"] = [
            literal_value(resolve_property_id(session, base, creds, "dcterms:description"), desc)
        ]
    moby_url = metadata_json.get("moby_url")
    if moby_url:
        payload["bibo:uri"] = [
            uri_value(resolve_property_id(session, base, creds, "bibo:uri"), moby_url)
        ]
    release = metadata_json.get("first_release_date")
    if release:
        payload["dcterms:date"] = [
            literal_value(resolve_property_id(session, base, creds, "dcterms:date"), release)
        ]
    publishers = metadata_json.get("publishers") or []
    if publishers:
        publisher_pid = resolve_property_id(session, base, creds, "dcterms:publisher")
        payload["dcterms:publisher"] = [literal_value(publisher_pid, name) for name in publishers]
    developers = metadata_json.get("developers") or []
    if developers:
        creator_pid = resolve_property_id(session, base, creds, "dcterms:creator")
        payload["dcterms:creator"] = [literal_value(creator_pid, name) for name in developers]
