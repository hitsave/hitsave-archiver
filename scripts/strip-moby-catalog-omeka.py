#!/usr/bin/env python3
"""Remove MobyGames catalog fields from ledger + Omeka when a match was wrong or absent."""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import psycopg
    import requests
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from omeka_catalog_common import load_register_dip_omeka_api  # noqa: E402
from omeka_staff_metadata import is_wasabi_s3_uri  # noqa: E402
from press_material_description import press_material_description  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

_MOBY_URI_MARK = "mobygames.com/game/"


def _is_moby_catalog_uri(uri: str) -> bool:
    return _MOBY_URI_MARK in (uri or "").casefold()


def _parse_json_field(raw: object) -> dict | list | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return json.loads(raw) if raw else None
    return raw


def strip_moby_catalog_row(
    register_api: object,
    session: requests.Session,
    base: str,
    creds: dict,
    db_cfg: dict,
    game_key: str,
    item_id: int,
    inventory_json: object,
    metadata_json: object,
) -> dict:
    inventory_json = _parse_json_field(inventory_json) or {}
    metadata_json = _parse_json_field(metadata_json) or {}
    if not isinstance(inventory_json, dict):
        inventory_json = {}
    if not isinstance(metadata_json, dict):
        metadata_json = {}

    title = register_api.resolve_omeka_item_title(game_key, metadata_json)
    desc = press_material_description(title, inventory_json)

    get_url = register_api.api_url(base, f"items/{item_id}", creds)
    resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"GET item {item_id} failed ({resp.status_code}): {resp.text[:800]}")
    item_body = resp.json()

    bibo_rows = []
    for row in item_body.get("bibo:uri") or []:
        uri = str(row.get("@id") or "")
        if _is_moby_catalog_uri(uri) or is_wasabi_s3_uri(uri):
            continue
        bibo_rows.append(dict(row))

    rights_rows = []
    for row in item_body.get("dcterms:rights") or []:
        text = str(row.get("@value") or "")
        if "mobygames" in text.casefold():
            continue
        rights_rows.append(dict(row))

    desc_pid = register_api.resolve_property_id(session, base, creds, "dcterms:description")
    updates = {
        "dcterms:description": [register_api.literal_value(desc_pid, desc)],
        "dcterms:date": [],
        "dcterms:publisher": [],
        "dcterms:creator": [],
        "dcterms:rights": rights_rows,
        "bibo:uri": bibo_rows,
    }
    register_api.patch_item_rdf_values(
        session, base, creds, int(item_id), updates, item_body=item_body
    )

    conn = register_api.pg_connect(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE game_ingest
                SET moby_game_id = NULL,
                    moby_status = 'no_match',
                    block_reason = 'moby_catalog_stripped',
                    metadata_json = NULL,
                    updated_at = NOW()
                WHERE game_key = %s
                """,
                (game_key,),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "game_key": game_key,
        "omeka_item_id": item_id,
        "stripped": True,
        "description": desc,
    }


def main() -> None:
    keys = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not keys:
        raise SystemExit("Usage: strip-moby-catalog-omeka.py <game_key> [...]")

    register_api = load_register_dip_omeka_api()
    api_cfg = load_yaml(CONFIG_ROOT / "omeka-catalog.yaml")["api"]
    cred_path = CONFIG_ROOT / api_cfg["credentials_file"]
    if cred_path.parts and cred_path.parts[0] == "config":
        cred_path = CONFIG_ROOT / Path(*cred_path.parts[1:])
    creds = load_yaml(cred_path)
    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]

    session = requests.Session()
    base = api_cfg["base_url"]

    conn = register_api.pg_connect(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, omeka_item_id, inventory_json, metadata_json
                FROM game_ingest
                WHERE game_key = ANY(%s) AND omeka_item_id IS NOT NULL
                """,
                (keys,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    results = []
    for game_key, item_id, inventory_json, metadata_json in rows:
        results.append(
            strip_moby_catalog_row(
                register_api,
                session,
                base,
                creds,
                db_cfg,
                game_key,
                int(item_id),
                inventory_json,
                metadata_json,
            )
        )

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
