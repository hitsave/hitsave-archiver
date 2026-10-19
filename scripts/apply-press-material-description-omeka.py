#!/usr/bin/env python3
"""PATCH dcterms:description from BagIt inventory (press kit wording, not Moby synopsis)."""
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

from omeka_catalog_common import load_register_dip_omeka_api  # noqa: E402
from press_material_description import press_material_description  # noqa: E402
from yaml_config import load_yaml  # noqa: E402


def _source_subpath_sql_like(batch_yaml: Path) -> str:
    raw = yaml.safe_load(batch_yaml.read_text()) or {}
    sub = str(raw.get("source_subpath") or "").strip().strip("/")
    if not sub:
        raise SystemExit(f"batch file must set source_subpath: {batch_yaml}")
    return f"%{sub}/%"


def main() -> None:
    argv = sys.argv[1:]
    batch_path: Path | None = None
    if "--batch" in argv:
        idx = argv.index("--batch")
        if idx + 1 >= len(argv):
            raise SystemExit("--batch requires a YAML path")
        batch_path = Path(argv[idx + 1])
        argv = argv[:idx] + argv[idx + 2 :]
    keys = [a for a in argv if not a.startswith("-")]

    register_api = load_register_dip_omeka_api()
    api_cfg = load_yaml(CONFIG_ROOT / "omeka-catalog.yaml")["api"]
    cred_rel = Path(api_cfg["credentials_file"])
    if cred_rel.parts and cred_rel.parts[0] == "config":
        cred_rel = Path(*cred_rel.parts[1:])
    creds = load_yaml(CONFIG_ROOT / cred_rel)
    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]

    session = requests.Session()
    base = api_cfg["base_url"]

    conn = register_api.pg_connect(db_cfg)
    try:
        with conn.cursor() as cur:
            if batch_path is not None:
                sql_like = _source_subpath_sql_like(batch_path)
                cur.execute(
                    """
                    SELECT game_key, omeka_item_id, inventory_json, metadata_json
                    FROM game_ingest
                    WHERE omeka_item_id IS NOT NULL
                      AND inventory_json IS NOT NULL
                      AND source_path LIKE %s
                    ORDER BY game_key
                    """,
                    (sql_like,),
                )
            elif keys:
                cur.execute(
                    """
                    SELECT game_key, omeka_item_id, inventory_json, metadata_json
                    FROM game_ingest
                    WHERE game_key = ANY(%s) AND omeka_item_id IS NOT NULL
                    ORDER BY game_key
                    """,
                    (keys,),
                )
            else:
                raise SystemExit(
                    "Usage: apply-press-material-description-omeka.py <game_key> [...] "
                    "| --batch config/preservation/batch-….yml"
                )
            rows = cur.fetchall()
    finally:
        conn.close()

    updated = 0
    for game_key, item_id, inventory_json, metadata_json in rows:
        if isinstance(inventory_json, str):
            inventory_json = json.loads(inventory_json)
        if isinstance(metadata_json, str):
            metadata_json = json.loads(metadata_json)
        get_url = register_api.api_url(base, f"items/{item_id}", creds)
        resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
        if not resp.ok:
            raise RuntimeError(f"GET item {item_id} failed ({resp.status_code}): {resp.text[:800]}")
        item_body = resp.json()
        title = register_api.omeka_item_catalog_title(item_body)
        if not title:
            title = register_api.resolve_omeka_item_title(game_key, metadata_json)
        desc = press_material_description(title, inventory_json)
        pid = register_api.resolve_property_id(session, base, creds, "dcterms:description")
        register_api.patch_item_rdf_values(
            session,
            base,
            creds,
            int(item_id),
            {"dcterms:description": [register_api.literal_value(pid, desc)]},
            item_body=item_body,
        )
        updated += 1
        print(json.dumps({"game_key": game_key, "omeka_item_id": item_id, "description": desc}))

    print(json.dumps({"updated_count": updated}, indent=2))


if __name__ == "__main__":
    main()
