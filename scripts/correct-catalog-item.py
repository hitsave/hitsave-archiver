#!/usr/bin/env python3
"""Apply optional Omeka catalog corrections for one ledger item (by Omeka id or URL)."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from moby_omeka_fields import moby_bibo_uri_rows, public_bibo_uri_rows_without_moby  # noqa: E402
from omeka_catalog_common import load_register_dip_omeka_api  # noqa: E402
from press_material_description import press_material_description  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

register_api = load_register_dip_omeka_api()


def _patch_wasabi_module():
    path = REPO_ROOT / "scripts" / "patch-press-catalog-omeka-and-bag.py"
    spec = importlib.util.spec_from_file_location("patch_press_catalog_omeka_and_bag", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_ITEM_ID_RE = re.compile(r"/item/(\d+)(?:/|$|\?)")


def parse_omeka_item_id(ref: str) -> int:
    raw = ref.strip()
    if raw.isdigit():
        return int(raw)
    m = _ITEM_ID_RE.search(raw)
    if m:
        return int(m.group(1))
    raise SystemExit(
        f"Could not parse Omeka item id from {ref!r} (use numeric id or a URL containing /item/<id>)"
    )


def _parse_json_field(raw: object) -> dict | list | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return json.loads(raw) if raw.strip() else None
    return raw


def _rights_rows_without_moby(item_body: dict) -> list[dict]:
    rows: list[dict] = []
    for row in item_body.get("dcterms:rights") or []:
        text = str(row.get("@value") or "")
        if "mobygames" in text.casefold():
            continue
        rows.append(dict(row))
    return rows


def _split_names(value: str | None) -> list[str] | None:
    if value is None:
        return None
    parts = [p.strip() for p in re.split(r"[,;]", str(value)) if p.strip()]
    return parts or None


def _ledger_row_by_omeka_item_id(item_id: int) -> dict:
    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    conn = register_api.pg_connect(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, omeka_item_id, package_storage_prefix, aip_wasabi_prefix,
                       title_slug, sort_letter, inventory_json, ingest_kind
                FROM game_ingest
                WHERE omeka_item_id = %s
                """,
                (item_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise SystemExit(f"No ledger row for omeka_item_id={item_id}")
    cols = [
        "game_key",
        "omeka_item_id",
        "package_storage_prefix",
        "aip_wasabi_prefix",
        "title_slug",
        "sort_letter",
        "inventory_json",
        "ingest_kind",
    ]
    out = dict(zip(cols, row))
    if out["omeka_item_id"] is None:
        raise SystemExit(f"Ledger row has no omeka_item_id: {out['game_key']!r}")
    return out


def _public_moby_uri(item_body: dict) -> str | None:
    for row in item_body.get("bibo:uri") or []:
        uri = (row.get("@id") or "").strip()
        if uri.startswith("s3://"):
            continue
        if "mobygames.com" in uri.casefold():
            return uri
    for row in item_body.get("bibo:uri") or []:
        uri = (row.get("@id") or "").strip()
        if uri and not uri.startswith("s3://"):
            return uri
    return None


def _catalog_title(item_body: dict) -> str:
    t = register_api.omeka_item_catalog_title(item_body)
    if t:
        return t.strip()
    ot = (item_body.get("o:title") or "").strip()
    if ot:
        return ot
    return ""


def _omeka_session() -> tuple[requests.Session, str, dict]:
    api_cfg = load_yaml(CONFIG_ROOT / "omeka-catalog.yaml")["api"]
    cred_rel = Path(api_cfg["credentials_file"])
    if cred_rel.parts and cred_rel.parts[0] == "config":
        cred_rel = Path(*cred_rel.parts[1:])
    creds = load_yaml(CONFIG_ROOT / cred_rel)
    return requests.Session(), api_cfg["base_url"], creds


def _fetch_item(session: requests.Session, base: str, creds: dict, item_id: int) -> dict:
    get_url = register_api.api_url(base, f"items/{item_id}", creds)
    resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"GET item {item_id} failed ({resp.status_code}): {resp.text[:800]}")
    return resp.json()


def _reset_ledger_moby_match(game_key: str) -> None:
    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    conn = register_api.pg_connect(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE game_ingest
                SET moby_game_id = NULL,
                    moby_title = NULL,
                    moby_status = 'no_match',
                    block_reason = 'moby_catalog_cleared',
                    metadata_json = NULL,
                    updated_at = NOW()
                WHERE game_key = %s
                """,
                (game_key,),
            )
        conn.commit()
    finally:
        conn.close()


def apply_omeka_corrections(
    *,
    item_id: int,
    title: str | None,
    moby_url: str | None,
    clear_moby_url: bool,
    description: str | None,
    developers: list[str] | None,
    publishers: list[str] | None,
    clear_developer: bool,
    clear_publisher: bool,
) -> dict[str, Any]:
    session, base, creds = _omeka_session()
    item_body = _fetch_item(session, base, creds, item_id)
    updates: dict[str, list] = {}

    if title is not None:
        title_pid = register_api.resolve_property_id(session, base, creds, "dcterms:title")
        updates["dcterms:title"] = [register_api.literal_value(title_pid, title)]

    if description is not None:
        desc_pid = register_api.resolve_property_id(session, base, creds, "dcterms:description")
        updates["dcterms:description"] = [register_api.literal_value(desc_pid, description)]

    if clear_moby_url:
        kept = public_bibo_uri_rows_without_moby(item_body)
        updates["bibo:uri"] = kept if kept else []
        updates["dcterms:date"] = []
        updates["dcterms:rights"] = _rights_rows_without_moby(item_body)
    elif moby_url is not None:
        bibo_pid = register_api.resolve_property_id(session, base, creds, "bibo:uri")
        updates["bibo:uri"] = moby_bibo_uri_rows(
            item_body, moby_url.strip(), bibo_pid, register_api.uri_value
        )

    if clear_publisher:
        updates["dcterms:publisher"] = []
    elif publishers is not None:
        publisher_pid = register_api.resolve_property_id(session, base, creds, "dcterms:publisher")
        updates["dcterms:publisher"] = [
            register_api.literal_value(publisher_pid, name) for name in publishers
        ]

    if clear_developer:
        updates["dcterms:creator"] = []
    elif developers is not None:
        creator_pid = register_api.resolve_property_id(session, base, creds, "dcterms:creator")
        updates["dcterms:creator"] = [
            register_api.literal_value(creator_pid, name) for name in developers
        ]

    if not updates:
        raise SystemExit("No Omeka field updates to apply")

    register_api.patch_item_rdf_values(
        session, base, creds, item_id, updates, item_body=item_body
    )

    if title is not None:
        patch_url = register_api.api_url(base, f"items/{item_id}", creds)
        title_patch = session.patch(
            patch_url,
            headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
            json={"o:title": title},
            timeout=120,
        )
        if not title_patch.ok:
            raise RuntimeError(
                f"o:title PATCH failed ({title_patch.status_code}): {title_patch.text[:800]}"
            )

    result = {
        "omeka_item_id": item_id,
        "patched_properties": sorted(updates.keys()),
    }
    print(json.dumps(result, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--omeka-ref",
        required=True,
        help="Omeka item id or public/admin/site URL containing /item/<id>",
    )
    parser.add_argument("--title")
    parser.add_argument("--moby-url", help="MobyGames game page URL (public bibo:uri)")
    parser.add_argument(
        "--clear-moby-url",
        action="store_true",
        help="Remove Moby catalog from Omeka: Moby bibo:uri, reset description to press-kit "
        "text (unless --description is set), strip Moby dcterms:rights and release date, "
        "clear dcterms:creator and dcterms:publisher unless --developer / --publisher is set",
    )
    parser.add_argument("--description")
    parser.add_argument("--developer", help="One name or comma-separated list (dcterms:creator)")
    parser.add_argument(
        "--clear-developer",
        action="store_true",
        help="Clear all dcterms:creator values on the item",
    )
    parser.add_argument("--publisher", help="One name or comma-separated list (dcterms:publisher)")
    parser.add_argument(
        "--clear-publisher",
        action="store_true",
        help="Clear all dcterms:publisher values on the item",
    )
    parser.add_argument(
        "--reset-ledger-moby",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When --clear-moby-url, clear ledger Moby match fields (default: on)",
    )
    parser.add_argument(
        "--former-title",
        help="Previous catalog title (for bag notes; default: current Omeka title before patch)",
    )
    parser.add_argument("--correction-summary", help="Human note stored in submission.json catalog_notes")
    parser.add_argument(
        "--execute",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Upload revised bag metadata to Wasabi when title or Moby URL changes (default: on)",
    )
    args = parser.parse_args()

    developers = _split_names(args.developer) if args.developer is not None else None
    publishers = _split_names(args.publisher) if args.publisher is not None else None
    title = (args.title or "").strip() or None
    moby_url = (args.moby_url or "").strip() or None
    description: str | None = None
    if args.description is not None:
        description = (args.description or "").strip()

    clear_flags = (
        args.clear_moby_url,
        args.clear_developer,
        args.clear_publisher,
    )
    correction_inputs = [
        title,
        moby_url,
        description if args.description is not None else None,
        developers,
        publishers,
    ]
    if not any(v is not None for v in correction_inputs) and not any(clear_flags):
        raise SystemExit(
            "Provide at least one correction field "
            "(--title, --moby-url, --description, --developer, --publisher, "
            "or --clear-moby-url / --clear-developer / --clear-publisher)"
        )
    if args.clear_moby_url and moby_url is not None:
        raise SystemExit("Use either --moby-url or --clear-moby-url, not both")

    item_id = parse_omeka_item_id(args.omeka_ref)
    ledger = _ledger_row_by_omeka_item_id(item_id)

    session, base, creds = _omeka_session()
    item_before = _fetch_item(session, base, creds, item_id)
    former_title = (args.former_title or "").strip() or _catalog_title(item_before)

    if args.clear_moby_url and args.description is None:
        inventory = _parse_json_field(ledger.get("inventory_json"))
        if not isinstance(inventory, dict):
            inventory = {}
        catalog_title = title or _catalog_title(item_before)
        description = press_material_description(catalog_title, inventory)

    clear_developer = args.clear_developer or (
        args.clear_moby_url and args.developer is None
    )
    clear_publisher = args.clear_publisher or (
        args.clear_moby_url and args.publisher is None
    )

    apply_omeka_corrections(
        item_id=item_id,
        title=title,
        moby_url=moby_url,
        clear_moby_url=args.clear_moby_url,
        description=description,
        developers=developers,
        publishers=publishers,
        clear_developer=clear_developer,
        clear_publisher=clear_publisher,
    )

    if args.clear_moby_url and args.reset_ledger_moby:
        _reset_ledger_moby_match(str(ledger["game_key"]))
        print(
            json.dumps(
                {"ledger_moby_reset": True, "game_key": ledger["game_key"]},
                indent=2,
            )
        )

    bag_title = title or _catalog_title(item_before)
    if args.clear_moby_url:
        bag_moby = ""
    else:
        bag_moby = moby_url or _public_moby_uri(item_before) or ""
    if title is not None or moby_url is not None or args.clear_moby_url:
        if not bag_moby and not args.clear_moby_url:
            print(
                json.dumps(
                    {
                        "wasabi_bag": "skipped",
                        "reason": "no Moby/reference URI on item; pass --moby-url to sync bag metadata",
                    },
                    indent=2,
                ),
                file=sys.stderr,
            )
        else:
            if args.clear_moby_url:
                summary = (args.correction_summary or "").strip() or (
                    f"Catalog correction ({date.today().isoformat()}): "
                    f"removed MobyGames catalog link; former title “{former_title}”."
                )
            else:
                summary = (args.correction_summary or "").strip() or (
                    f"Catalog correction ({date.today().isoformat()}): "
                    f"former title “{former_title}”; reference {bag_moby}."
                )
            ingest_kind = str(ledger.get("ingest_kind") or "press_material").strip() or "press_material"
            patch_mod = _patch_wasabi_module()
            patch_mod.patch_wasabi_bag(
                ledger=ledger,
                title=bag_title,
                reference_uri=bag_moby,
                former_title=former_title,
                correction_summary=summary,
                execute=args.execute,
                ingest_kind=ingest_kind,
            )


if __name__ == "__main__":
    main()
