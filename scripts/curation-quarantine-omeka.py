#!/usr/bin/env python3
"""Staff curation: flag ledger quarantine + hide Omeka item/DIP media (post-ingest)."""
from __future__ import annotations

import argparse
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


def _ledger_connect(register_api, ingest_cfg):
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    return register_api.pg_connect(db_cfg)


def _fetch_row(conn, *, game_key: str | None, omeka_item_id: int | None) -> tuple[str, int | None, int | None]:
    with conn.cursor() as cur:
        if omeka_item_id is not None:
            cur.execute(
                """
                SELECT game_key, omeka_item_id, omeka_media_id
                FROM game_ingest
                WHERE omeka_item_id = %s
                """,
                (omeka_item_id,),
            )
        else:
            cur.execute(
                """
                SELECT game_key, omeka_item_id, omeka_media_id
                FROM game_ingest
                WHERE game_key = %s
                """,
                (game_key,),
            )
        row = cur.fetchone()
    if not row:
        if omeka_item_id is not None:
            raise SystemExit(f"No ledger row for omeka_item_id={omeka_item_id}")
        raise SystemExit(f"Unknown game_key: {game_key}")
    return row[0], row[1], row[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    id_group = parser.add_mutually_exclusive_group(required=True)
    id_group.add_argument(
        "--game-key",
        help="Ledger game_key (full package_id)",
    )
    id_group.add_argument(
        "--omeka-item-id",
        type=int,
        help="Resolve game_key from game_ingest.omeka_item_id (e.g. ARK name segment)",
    )
    parser.add_argument(
        "--reason",
        default="curation_review",
        help="Ledger block_reason code (e.g. hash_named_images, booru_tagged_filenames)",
    )
    parser.add_argument(
        "note",
        nargs="+",
        help="Human-readable curation note (required)",
    )
    args = parser.parse_args()
    note = " ".join(args.note).strip()
    if not note:
        raise SystemExit("Curation note is required.")

    register_api = load_register_dip_omeka_api()
    api_cfg = load_yaml(CONFIG_ROOT / "omeka-catalog.yaml")["api"]
    cred_path = CONFIG_ROOT / api_cfg["credentials_file"]
    if cred_path.parts and cred_path.parts[0] == "config":
        cred_path = CONFIG_ROOT / Path(*cred_path.parts[1:])
    creds = load_yaml(cred_path)
    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")

    session = requests.Session()
    base = api_cfg["base_url"]

    conn = _ledger_connect(register_api, ingest_cfg)
    try:
        game_key, item_id, media_id = _fetch_row(
            conn,
            game_key=args.game_key,
            omeka_item_id=args.omeka_item_id,
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE game_ingest
                SET status = 'quarantine',
                    block_reason = %s,
                    error_message = %s,
                    updated_at = NOW()
                WHERE game_key = %s
                """,
                (args.reason, note, game_key),
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
                "block_reason": args.reason,
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
