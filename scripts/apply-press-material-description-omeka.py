#!/usr/bin/env python3
"""PATCH dcterms:description from BagIt inventory (press kit wording, not Moby synopsis)."""
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
from press_material_description import press_material_description  # noqa: E402
from yaml_config import load_yaml  # noqa: E402


def main() -> None:
    keys = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not keys:
        raise SystemExit("Usage: apply-press-material-description-omeka.py <game_key> [...]")

    register_api = load_register_dip_omeka_api()
    api_cfg = load_yaml(CONFIG_ROOT / "omeka-catalog.yaml")["api"]
    cred_path = CONFIG_ROOT / "secrets" / "omeka-api-credentials-local.yaml"
    if not cred_path.is_file():
        cred_path = CONFIG_ROOT / api_cfg["credentials_file"]
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

    for game_key, item_id, inventory_json, metadata_json in rows:
        if isinstance(inventory_json, str):
            inventory_json = json.loads(inventory_json)
        if isinstance(metadata_json, str):
            metadata_json = json.loads(metadata_json)
        title = register_api.resolve_omeka_item_title(game_key, metadata_json)
        desc = press_material_description(title, inventory_json)
        pid = register_api.resolve_property_id(session, base, creds, "dcterms:description")
        register_api.patch_item_rdf_values(
            session,
            base,
            creds,
            int(item_id),
            {"dcterms:description": [register_api.literal_value(pid, desc)]},
        )
        print(json.dumps({"game_key": game_key, "omeka_item_id": item_id, "description": desc}))


if __name__ == "__main__":
    main()
