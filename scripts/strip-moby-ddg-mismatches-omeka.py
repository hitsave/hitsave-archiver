#!/usr/bin/env python3
"""Strip Omeka Moby catalog rows where DuckDuckGo fallback title does not match Moby title."""
from __future__ import annotations

import importlib.util
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

from moby_resolve import moby_title_matches_search  # noqa: E402
from omeka_catalog_common import load_register_dip_omeka_api  # noqa: E402
from yaml_config import load_yaml  # noqa: E402


def _load_strip_module():
    path = REPO_ROOT / "scripts" / "strip-moby-catalog-omeka.py"
    spec = importlib.util.spec_from_file_location("strip_moby_catalog_omeka", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    register_api = load_register_dip_omeka_api()
    api_cfg = load_yaml(CONFIG_ROOT / "omeka-catalog.yaml")["api"]
    cred_path = CONFIG_ROOT / api_cfg["credentials_file"]
    if cred_path.parts and cred_path.parts[0] == "config":
        cred_path = CONFIG_ROOT / Path(*cred_path.parts[1:])
    creds = load_yaml(cred_path)
    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]

    conn = register_api.pg_connect(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, omeka_item_id, inventory_json, metadata_json
                FROM game_ingest
                WHERE omeka_item_id IS NOT NULL
                  AND metadata_json IS NOT NULL
                  AND metadata_json->>'moby_game_id' IS NOT NULL
                  AND metadata_json->>'moby_match_source' = 'duckduckgo'
                ORDER BY omeka_item_id
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    to_strip: list[tuple] = []
    kept: list[dict] = []
    for game_key, item_id, inventory_json, metadata_json in rows:
        if isinstance(metadata_json, str):
            metadata_json = json.loads(metadata_json)
        search_title = str(metadata_json.get("search_title") or "").strip()
        moby_title = str(
            metadata_json.get("moby_title") or metadata_json.get("title") or ""
        ).strip()
        if moby_title_matches_search(moby_title, search_title):
            kept.append(
                {
                    "game_key": game_key,
                    "omeka_item_id": item_id,
                    "search_title": search_title,
                    "moby_title": moby_title,
                }
            )
            continue
        to_strip.append((game_key, item_id, inventory_json, metadata_json))

    if dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "strip_count": len(to_strip),
                    "keep_count": len(kept),
                    "strip": [
                        {
                            "game_key": r[0],
                            "omeka_item_id": r[1],
                            "search_title": (r[3] or {}).get("search_title"),
                            "moby_title": (r[3] or {}).get("moby_title"),
                        }
                        for r in to_strip
                    ],
                },
                indent=2,
            )
        )
        return

    strip_mod = _load_strip_module()
    session = requests.Session()
    base = api_cfg["base_url"]
    stripped = []
    errors = []
    for game_key, item_id, inventory_json, metadata_json in to_strip:
        try:
            stripped.append(
                strip_mod.strip_moby_catalog_row(
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
        except Exception as e:
            errors.append({"game_key": game_key, "omeka_item_id": item_id, "error": str(e)})

    print(
        json.dumps(
            {
                "strip_count": len(stripped),
                "keep_count": len(kept),
                "error_count": len(errors),
                "stripped": stripped,
                "errors": errors,
            },
            indent=2,
        )
    )
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
