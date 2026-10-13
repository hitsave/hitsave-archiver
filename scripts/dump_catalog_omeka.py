#!/usr/bin/env python3
"""Omeka catalog RDF for ingest kind dump (Redump sidecar + ledger Wasabi prefix + Moby)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

from dump_classify import classify_dump_sip, find_submission_info_path
from dump_redump import parse_submission_info
from ingest_kind import ingest_kind_spec


def _redump_field(fields: dict[str, str], key: str) -> str | None:
    raw = (fields.get(key) or "").strip()
    if not raw or raw.startswith("(OPTIONAL)") or raw.startswith("(REQUIRED"):
        return None
    return raw


def _catalog_alternates(title: str, foreign: str | None, alternates: list[str]) -> list[str]:
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


def staff_storage_uri(
    *,
    manifest: dict[str, Any] | None = None,
    ledger_aip_wasabi_prefix: str | None = None,
    ledger_package_storage_prefix: str | None = None,
    bucket: str | None = None,
    wasabi_ingest_cfg: dict | None = None,
    storage_uri_base: str = "",
    game_key: str = "",
) -> str:
    """Prefer s3:// ledger prefix; fall back to handoff manifest wasabi block."""
    if ledger_aip_wasabi_prefix and str(ledger_aip_wasabi_prefix).startswith("s3://"):
        return str(ledger_aip_wasabi_prefix).rstrip("/") + "/"
    wasabi = (manifest or {}).get("wasabi") or {}
    bkt = bucket
    if not bkt and wasabi_ingest_cfg:
        from preservation_common import resolve_wasabi_bucket

        bkt = resolve_wasabi_bucket(wasabi_ingest_cfg, "dump")
    pfx = (
        ledger_package_storage_prefix
        or wasabi.get("package_storage_prefix")
        or (manifest or {}).get("package_storage_prefix")
    )
    if bkt and pfx:
        return f"s3://{bkt}/{str(pfx).strip('/')}/"
    legacy = wasabi.get("prefix")
    if bkt and legacy:
        leg = str(legacy).strip("/")
        if not leg.startswith("dumps/"):
            leg = f"dumps/{leg}" if leg else f"dumps/Others/{game_key}"
        return f"s3://{bkt}/{leg}/"
    if game_key and storage_uri_base:
        return f"{storage_uri_base.rstrip('/')}/{game_key}/"
    return ""


def manifest_from_sip_and_handoff(
    *,
    source_dir: Path,
    game_key: str,
    omeka_title: str,
    handoff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    handoff = handoff or {}
    clf = classify_dump_sip(source_dir, package_id=game_key)
    redump_block = dict(handoff.get("redump") or {})
    parsed = (redump_block.get("parsed") or {}).copy()
    dump_layout = (ingest_kind_spec("dump").get("dump_layout") or {}).get("redump") or {}
    sub_names = list(dump_layout.get("submission_info_names") or [])
    sub_path = find_submission_info_path(source_dir, sub_names) if sub_names else None
    if not parsed.get("fields") and sub_path is not None:
        submission = parse_submission_info(sub_path.read_text(encoding="utf-8", errors="replace"))
        parsed = {
            "fields": submission.fields,
            "checksums": {
                "size": submission.checksums.size,
                "crc32": submission.checksums.crc32,
                "md5": submission.checksums.md5,
                "sha1": submission.checksums.sha1,
            },
            "alternate_titles": submission.alternate_titles,
        }
    manifest: dict[str, Any] = {
        "game_key": game_key,
        "omeka_item_title": omeka_title,
        "ingest_kind": "dump",
        "dump_class": clf.dump_class,
        "dump_system": clf.dump_system,
        "dump_category": clf.dump_category,
        "package_storage_prefix": clf.package_storage_prefix,
        "wasabi": {
            "bucket": (handoff.get("wasabi") or {}).get("bucket"),
            "package_storage_prefix": clf.package_storage_prefix,
        },
        "redump": {"parsed": parsed},
    }
    if handoff.get("wasabi"):
        manifest["wasabi"].update(
            {k: v for k, v in handoff["wasabi"].items() if k not in manifest["wasabi"]}
        )
    return manifest


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
    ledger_aip_wasabi_prefix: str | None = None,
    ledger_package_storage_prefix: str | None = None,
    wasabi_ingest_cfg: dict | None = None,
) -> tuple[dict[str, list], str]:
    from redump_info_lookup import redump_disc_page_url, resolve_redump_disc_by_sha1

    fields = (manifest.get("redump") or {}).get("parsed", {}).get("fields") or {}
    alternates = (manifest.get("redump") or {}).get("parsed", {}).get("alternate_titles") or []
    wasabi = manifest.get("wasabi") or {}
    fallback_title = str(manifest.get("omeka_item_title") or fields.get("Title") or "")

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

    def append_literal(term: str, value: str | None) -> None:
        if not value or not str(value).strip():
            return
        pid = api.resolve_property_id(session, base, creds, term)
        values.setdefault(term, []).append(api.literal_value(pid, str(value).strip()))

    append_literal("dcterms:title", title)
    for alt in _catalog_alternates(title, foreign, extra_alts):
        append_literal("dcterms:alternative", alt)

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

    for fmt in (_redump_field(fields, "System"), _redump_field(fields, "Media Type")):
        append_literal("dcterms:format", fmt)
    append_literal("dcterms:type", _redump_field(fields, "Category"))
    append_literal("dcterms:coverage", _redump_field(fields, "Region"))
    append_literal("dcterms:language", _redump_field(fields, "Languages"))

    staff_uri = staff_storage_uri(
        manifest=manifest,
        ledger_aip_wasabi_prefix=ledger_aip_wasabi_prefix,
        ledger_package_storage_prefix=ledger_package_storage_prefix
        or manifest.get("package_storage_prefix"),
        wasabi_ingest_cfg=wasabi_ingest_cfg,
        storage_uri_base=storage_uri_base,
        game_key=str(manifest.get("game_key") or ""),
    )
    if staff_uri:
        bibo_pid = api.resolve_property_id(session, base, creds, "bibo:uri")
        values["bibo:uri"] = [api.uri_value(bibo_pid, staff_uri)]

    if disc_id is not None:
        redump_url = redump_disc_page_url(disc_id, cfg)
        relation_pid = api.resolve_property_id(session, base, creds, "dcterms:relation")
        values["dcterms:relation"] = [api.uri_value(relation_pid, redump_url)]

    if description:
        append_literal("dcterms:description", description)
    else:
        values["dcterms:description"] = []
    values["dcterms:source"] = []
    return values, title


def dump_catalog_config(uploader_cfg: dict[str, Any]) -> dict[str, Any]:
    return dict(uploader_cfg.get("dump_catalog") or uploader_cfg.get("redump_catalog") or {})
