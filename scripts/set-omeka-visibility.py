#!/usr/bin/env python3
"""Set Omeka item + DIP media public or private (post-ingest curation)."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import requests
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from omeka_catalog_common import load_register_dip_omeka_api, set_resource_public  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

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


def _ledger_ids(register_api, ingest_cfg, omeka_item_id: int) -> tuple[str, int | None, int | None]:
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    conn = register_api.pg_connect(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, omeka_item_id, omeka_media_id
                FROM game_ingest
                WHERE omeka_item_id = %s
                """,
                (omeka_item_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise SystemExit(f"No ledger row for omeka_item_id={omeka_item_id}")
    return row[0], row[1], row[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--omeka-ref",
        required=True,
        help="Omeka item id or URL containing /item/<id>",
    )
    vis = parser.add_mutually_exclusive_group(required=True)
    vis.add_argument("--public", action="store_true", help="Show item and DIP media on the public site")
    vis.add_argument("--private", action="store_true", help="Hide item and DIP media from the public site")
    args = parser.parse_args()

    item_id = parse_omeka_item_id(args.omeka_ref)
    is_public = bool(args.public)

    register_api = load_register_dip_omeka_api()
    api_cfg = load_yaml(CONFIG_ROOT / "omeka-catalog.yaml")["api"]
    cred_path = CONFIG_ROOT / api_cfg["credentials_file"]
    if cred_path.parts and cred_path.parts[0] == "config":
        cred_path = CONFIG_ROOT / Path(*cred_path.parts[1:])
    creds = load_yaml(cred_path)
    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")

    game_key, ledger_item_id, media_id = _ledger_ids(register_api, ingest_cfg, item_id)
    if ledger_item_id is None:
        raise SystemExit(f"Ledger row {game_key!r} has no omeka_item_id")

    session = requests.Session()
    base = api_cfg["base_url"]
    updated: list[dict] = []
    for resource, rid in (("items", int(ledger_item_id)), ("media", media_id)):
        if rid is None:
            continue
        set_resource_public(
            session,
            base,
            creds,
            resource,
            int(rid),
            is_public=is_public,
            api=register_api,
        )
        updated.append({"resource": resource, "id": int(rid), "o:is_public": is_public})

    print(
        json.dumps(
            {
                "game_key": game_key,
                "omeka_item_id": ledger_item_id,
                "omeka_media_id": media_id,
                "is_public": is_public,
                "updated": updated,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
