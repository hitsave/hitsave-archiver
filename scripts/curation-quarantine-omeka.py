#!/usr/bin/env python3
"""Staff curation: flag ledger quarantine + hide Omeka item/DIP media (post-ingest)."""
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

from omeka_catalog_common import load_register_dip_omeka_api, set_resource_public  # noqa: E402
from yaml_config import load_yaml  # noqa: E402


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        raise SystemExit(
            "Usage: curation-quarantine-omeka.py <game_key> [--reason CODE] <note...>"
        )
    game_key = argv[0]
    reason = "curation_review"
    rest = argv[1:]
    if rest and rest[0] == "--reason":
        if len(rest) < 2:
            raise SystemExit("--reason requires a code (e.g. fan_book)")
        reason = rest[1].strip()
        rest = rest[2:]
    note = " ".join(rest).strip()
    if not note:
        raise SystemExit("Curation note is required (describe why this row is quarantined).")

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
                SELECT omeka_item_id, omeka_media_id
                FROM game_ingest
                WHERE game_key = %s
                """,
                (game_key,),
            )
            row = cur.fetchone()
            if not row:
                raise SystemExit(f"Unknown game_key: {game_key}")
            item_id, media_id = row[0], row[1]
            cur.execute(
                """
                UPDATE game_ingest
                SET status = 'quarantine',
                    block_reason = %s,
                    error_message = %s,
                    updated_at = NOW()
                WHERE game_key = %s
                """,
                (reason, note, game_key),
            )
        conn.commit()
    finally:
        conn.close()

    hidden: list[int] = []
    if item_id:
        set_resource_public(
            session,
            base,
            creds,
            "items",
            int(item_id),
            is_public=False,
            api=register_api,
        )
        hidden.append(int(item_id))
    if media_id:
        set_resource_public(
            session,
            base,
            creds,
            "media",
            int(media_id),
            is_public=False,
            api=register_api,
        )
        hidden.append(int(media_id))

    print(
        json.dumps(
            {
                "game_key": game_key,
                "ledger_status": "quarantine",
                "block_reason": reason,
                "error_message": note,
                "omeka_item_id": item_id,
                "omeka_media_id": media_id,
                "omeka_hidden_ids": hidden,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
