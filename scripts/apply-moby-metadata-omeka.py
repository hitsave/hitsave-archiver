#!/usr/bin/env python3
"""Apply ledger Moby metadata to existing Omeka items (REST PATCH)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import psycopg
    import requests
    import yaml
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))

sys.path.insert(0, str(REPO_ROOT / "scripts"))

from ingest_kind import resolve_omeka_moby_profile  # noqa: E402
from moby_resolve import load_mobygames_config, uses_moby_catalog_data  # noqa: E402
from omeka_catalog_common import load_register_dip_omeka_api  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

# Ledger rows ingested before ingest_kind column was populated (Omeka routing only).
_LEGACY_OMEKA_INGEST_KIND = "press_material"


def main() -> None:
    apply_all = "--all" in sys.argv
    keys = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not apply_all and not keys:
        raise SystemExit("Usage: apply-moby-metadata-omeka.py [--all] [game_key ...]")

    register_api = load_register_dip_omeka_api()
    pg_connect = register_api.pg_connect
    apply_moby_metadata_to_item = register_api.apply_moby_metadata_to_item

    api_cfg = load_yaml(CONFIG_ROOT / "omeka-catalog.yaml")["api"]
    cred_rel = Path(api_cfg["credentials_file"])
    if cred_rel.parts and cred_rel.parts[0] == "config":
        cred_rel = Path(*cred_rel.parts[1:])
    creds_path = CONFIG_ROOT / cred_rel
    creds = load_yaml(creds_path)
    moby_cfg = load_mobygames_config(CONFIG_ROOT)

    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]

    session = requests.Session()
    base = api_cfg["base_url"]

    conn = pg_connect(db_cfg)
    try:
        with conn.cursor() as cur:
            if apply_all:
                cur.execute(
                    """
                    SELECT game_key, omeka_item_id, metadata_json, inventory_json, ingest_kind
                    FROM game_ingest
                    WHERE omeka_item_id IS NOT NULL AND metadata_json IS NOT NULL
                    ORDER BY game_key
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT game_key, omeka_item_id, metadata_json, inventory_json, ingest_kind
                    FROM game_ingest
                    WHERE game_key = ANY(%s) AND omeka_item_id IS NOT NULL
                    ORDER BY game_key
                    """,
                    (keys,),
                )
            rows = cur.fetchall()
    finally:
        conn.close()

    results = []
    for game_key, item_id, metadata_json, inventory_json, ingest_kind in rows:
        if isinstance(metadata_json, str):
            metadata_json = json.loads(metadata_json)
        if isinstance(inventory_json, str):
            inventory_json = json.loads(inventory_json)
        if not metadata_json or not uses_moby_catalog_data(metadata_json):
            results.append(
                {"game_key": game_key, "omeka_item_id": item_id, "skipped": True, "reason": "no_moby"}
            )
            continue
        kind = (str(ingest_kind).strip() if ingest_kind else "") or _LEGACY_OMEKA_INGEST_KIND
        omeka_profile = resolve_omeka_moby_profile(ingest_kind=kind)
        fields = apply_moby_metadata_to_item(
            session,
            base,
            creds,
            int(item_id),
            metadata_json,
            moby_cfg,
            game_key=game_key,
            inventory_json=inventory_json,
            omeka_profile=omeka_profile,
        )
        results.append(
            {
                "game_key": game_key,
                "omeka_item_id": item_id,
                "moby_fields_applied": fields,
            }
        )

    print(json.dumps({"count": len(results), "results": results}, indent=2))


if __name__ == "__main__":
    main()
