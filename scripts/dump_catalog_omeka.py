#!/usr/bin/env python3
"""Omeka catalog RDF for ingest kind dump (Redump sidecar + ledger Wasabi prefix + Moby)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import requests

from dump_classify import classify_dump_sip, find_submission_info_path
from package_identity import sort_letter as catalog_sort_letter
from hitsave_dump_sidecars.redump import (
    RedumpSubmission,
    parse_submission_info,
    pick_omeka_title_from_submission,
    redump_catalog_sha1,
)
from dump_switch import (
    parse_switch_submission_xml,
    resolve_switch_submission_path,
    switch_game_title,
    switch_version_label,
)
from ingest_kind import ingest_kind_spec


def _redump_field(fields: dict[str, str], key: str) -> str | None:
    raw = (fields.get(key) or "").strip()
    if not raw or raw.startswith("(OPTIONAL)") or raw.startswith("(REQUIRED"):
        return None
    return raw


def _omeka_format_from_redump(field: str, value: str | None) -> str | None:
    """Map Redump submissionInfo labels to Omeka dcterms:format display (e.g. DVD-ROM-5 → DVD-5)."""
    if not value:
        return None
    text = value.strip()
    if field == "Media Type":
        media = re.fullmatch(r"DVD-ROM-(\d+)", text, flags=re.IGNORECASE)
        if media:
            return f"DVD-{media.group(1)}"
    return text


def _redump_languages(fields: dict[str, str]) -> str | None:
    """Redump template leaves Languages unset as an instruction line — not a real value."""
    raw = _redump_field(fields, "Languages")
    if raw is None:
        return None
    normalized = " ".join(raw.split()).casefold()
    if normalized.startswith("add languages here"):
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
    if game_key and storage_uri_base:
        return f"{storage_uri_base.rstrip('/')}/{game_key}/"
    return ""


def manifest_from_sip_and_handoff(
    *,
    source_dir: Path,
    game_key: str,
    omeka_title: str,
    handoff: dict[str, Any] | None = None,
    sort_letter: str | None = None,
) -> dict[str, Any]:
    handoff = handoff or {}
    letter = (sort_letter or catalog_sort_letter(omeka_title) or "X").strip() or "X"
    clf = classify_dump_sip(source_dir, package_id=game_key, sort_letter=letter)
    switch_block = dict(handoff.get("switch") or {})
    redump_block = dict(handoff.get("redump") or {})
    parsed = (redump_block.get("parsed") or {}).copy()
    dump_layout = (ingest_kind_spec("dump").get("dump_layout") or {}).get("redump") or {}
    sub_names = list(dump_layout.get("submission_info_names") or [])
    sub_path = find_submission_info_path(source_dir, sub_names) if sub_names else None
    if clf.dump_class == "switch" and not switch_block.get("parsed"):
        sw_path = resolve_switch_submission_path(source_dir)
        if sw_path is not None:
            sw = parse_switch_submission_xml(sw_path.read_text(encoding="utf-8", errors="replace"))
            primary = sw.primary_cart_image()
            version_label = str(
                switch_block.get("version") or switch_version_label(source_dir, sw_path)
            )
            game_title = str(
                switch_block.get("game_title") or switch_game_title(source_dir, sw)
            )
            switch_block["parsed"] = {
                "game_name": sw.game_name,
                "game_title": game_title,
                "version": version_label,
                "region": sw.region,
                "gameid1": sw.gameid1,
                "gameid2": sw.gameid2,
                "categories": sw.categories,
                "dumper": sw.dumper,
                "project": sw.project,
                "tool": sw.tool,
                "primary_image": {
                    "size": primary.size if primary else None,
                    "sha256": primary.sha256 if primary else None,
                    "sha1": primary.sha1 if primary else None,
                    "md5": primary.md5 if primary else None,
                },
            }

    if sub_path is not None:
        submission = parse_submission_info(sub_path.read_text(encoding="utf-8", errors="replace"))
        lookup_sha1 = redump_catalog_sha1(submission)
        if not parsed.get("fields"):
            parsed = {
                "fields": submission.fields,
                "checksums": {
                    "size": submission.checksums.size,
                    "crc32": submission.checksums.crc32,
                    "md5": submission.checksums.md5,
                    "sha1": lookup_sha1 or submission.checksums.sha1,
                },
                "alternate_titles": submission.alternate_titles,
                "roms": [
                    {
                        "name": rom.name,
                        "size": rom.size,
                        "crc32": rom.crc32,
                        "md5": rom.md5,
                        "sha1": rom.sha1,
                    }
                    for rom in submission.roms
                ],
            }
        else:
            checksums = dict(parsed.get("checksums") or {})
            if lookup_sha1 and not checksums.get("sha1"):
                checksums["sha1"] = lookup_sha1
                parsed["checksums"] = checksums
            if submission.roms and not parsed.get("roms"):
                parsed["roms"] = [
                    {
                        "name": rom.name,
                        "size": rom.size,
                        "crc32": rom.crc32,
                        "md5": rom.md5,
                        "sha1": rom.sha1,
                    }
                    for rom in submission.roms
                ]
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
        "switch": switch_block,
    }
    if handoff.get("wasabi"):
        manifest["wasabi"].update(
            {k: v for k, v in handoff["wasabi"].items() if k not in manifest["wasabi"]}
        )
    return manifest


def build_switch_values(
    session: requests.Session,
    base: str,
    creds: dict,
    manifest: dict[str, Any],
    *,
    storage_uri_base: str,
    api,
    existing_item: dict | None = None,
    ledger_aip_wasabi_prefix: str | None = None,
    ledger_package_storage_prefix: str | None = None,
    wasabi_ingest_cfg: dict | None = None,
) -> tuple[dict[str, list], str]:
    parsed = (manifest.get("switch") or {}).get("parsed") or {}
    game_title = str(parsed.get("game_title") or parsed.get("game_name") or "").strip()
    version = str(parsed.get("version") or "").strip()
    if not game_title:
        game_title = str(manifest.get("omeka_item_title") or "").strip()
    catalog_title = f"{game_title} ({version})" if version else game_title

    values: dict[str, list] = {}

    def append_literal(term: str, value: str | None) -> None:
        if not value or not str(value).strip():
            return
        pid = api.resolve_property_id(session, base, creds, term)
        row = api.literal_value(pid, str(value).strip())
        row["is_public"] = True
        values.setdefault(term, []).append(row)

    append_literal("dcterms:title", game_title)
    if version:
        append_literal("dcterms:description", f"Version {version}")
    append_literal("dcterms:format", "Nintendo Switch")
    append_literal("dcterms:type", parsed.get("categories") or "Games")
    append_literal("dcterms:coverage", parsed.get("region"))
    gameid1 = parsed.get("gameid1")
    if gameid1:
        id_rows: list[dict] = []
        if existing_item is not None:
            for row in existing_item.get("dcterms:identifier") or []:
                val = str(row.get("@value") or "")
                if val.startswith("ark:"):
                    id_rows.append(row)
        pid = api.resolve_property_id(session, base, creds, "dcterms:identifier")
        id_rows.append(api.literal_value(pid, str(gameid1)))
        values["dcterms:identifier"] = id_rows

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
        values["bibo:uri"] = [api.uri_value(bibo_pid, staff_uri, is_public=False)]

    values.setdefault("dcterms:description", [])
    values.setdefault("dcterms:source", [])
    values.setdefault("dcterms:relation", [])
    return values, catalog_title


def build_dump_catalog_values(
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
    if manifest.get("dump_class") == "switch":
        return build_switch_values(
            session,
            base,
            creds,
            manifest,
            storage_uri_base=storage_uri_base,
            api=api,
            existing_item=existing_item,
            ledger_aip_wasabi_prefix=ledger_aip_wasabi_prefix,
            ledger_package_storage_prefix=ledger_package_storage_prefix,
            wasabi_ingest_cfg=wasabi_ingest_cfg,
        )
    return build_redump_values(
        session,
        base,
        creds,
        manifest,
        storage_uri_base=storage_uri_base,
        api=api,
        redump_cfg=redump_cfg,
        existing_item=existing_item,
        ledger_aip_wasabi_prefix=ledger_aip_wasabi_prefix,
        ledger_package_storage_prefix=ledger_package_storage_prefix,
        wasabi_ingest_cfg=wasabi_ingest_cfg,
    )


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
    sidecar = RedumpSubmission(
        fields=dict(fields),
        alternate_titles=list(alternates) if isinstance(alternates, list) else [],
    )
    fallback_title = (
        str(manifest.get("omeka_item_title") or "").strip()
        or pick_omeka_title_from_submission(sidecar)
        or str(fields.get("Title") or "").strip()
    )

    cfg = redump_cfg or {}
    checksums = (manifest.get("redump") or {}).get("parsed", {}).get("checksums") or {}
    sha1 = checksums.get("sha1") or fields.get("SHA1")
    if not sha1:
        roms = (manifest.get("redump") or {}).get("parsed", {}).get("roms") or []
        if roms and isinstance(roms[0], dict) and roms[0].get("sha1"):
            sha1 = roms[0]["sha1"]
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
        row = api.literal_value(pid, str(value).strip())
        row["is_public"] = True
        values.setdefault(term, []).append(row)

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

    for field in ("System", "Media Type"):
        append_literal(
            "dcterms:format",
            _omeka_format_from_redump(field, _redump_field(fields, field)),
        )
    append_literal("dcterms:type", _redump_field(fields, "Category"))
    append_literal("dcterms:coverage", _redump_field(fields, "Region"))
    append_literal("dcterms:language", _redump_languages(fields))

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
        storage_public = bool(cfg.get("storage_uri_is_public", False))
        values["bibo:uri"] = [
            api.uri_value(bibo_pid, staff_uri, is_public=storage_public)
        ]

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
    from config_require import require_bool, require_key, require_str

    dump = uploader_cfg.get("dump_catalog")
    if not isinstance(dump, dict):
        raise ValueError("omeka-catalog.yaml must define dump_catalog")
    where = "omeka-catalog.yaml dump_catalog"
    ignore_ledger = dump.get("ignore_ledger_omeka_item_id", False)
    if not isinstance(ignore_ledger, bool):
        raise ValueError(f"{where}.ignore_ledger_omeka_item_id must be a boolean")
    item_set_id = dump.get("item_set_id")
    if item_set_id is not None:
        item_set_id = int(item_set_id)
    return {
        "item_set_title": require_str(dump, "item_set_title", where=where),
        "item_set_id": item_set_id,
        "resource_template_label": require_str(dump, "resource_template_label", where=where),
        "ignore_ledger_omeka_item_id": ignore_ledger,
        "storage_uri_is_public": require_bool(dump, "storage_uri_is_public", where=where),
        "is_public": require_bool(dump, "is_public", where=where),
        "storage_uri_base": require_str(dump, "storage_uri_base", where=where),
        "site_url": require_str(dump, "site_url", where=where),
        "disc_url": require_str(dump, "disc_url", where=where),
        "lookup_timeout_seconds": int(require_key(dump, "lookup_timeout_seconds", where=where)),
        "user_agent": require_str(dump, "user_agent", where=where),
    }
