#!/usr/bin/env python3
"""Run Moby lookup for one game and persist on the ledger row."""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import psycopg
    import yaml
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from moby_resolve import (  # noqa: E402
    finalize_moby_metadata,
    load_moby_config,
    resolve_for_game,
    uses_moby_catalog_data,
)
from preservation_common import resolve_game_config  # noqa: E402


def main() -> None:
    game_cfg_path = Path(sys.argv[1])
    game_cfg = resolve_game_config(
        yaml.safe_load(game_cfg_path.read_text()),
        agent_version="moby_enrich_game.py",
    )
    game_key = game_cfg["game_key"]
    folder_name = Path(game_cfg["source_game_folder"]).name
    display_title = game_cfg.get("omeka_item_title")

    config_root = Path("/config") if Path("/config/mobygames.yaml").is_file() else REPO_ROOT / "config"
    moby_cfg = load_moby_config(config_root).get("mobygames") or {}
    result = resolve_for_game(
        config_root=config_root,
        folder_name=folder_name,
        display_title=display_title,
        moby_game_id=game_cfg.get("moby_game_id"),
        moby_search_title=game_cfg.get("moby_search_title"),
    )
    if result.metadata_json and uses_moby_catalog_data(result.metadata_json):
        finalize_moby_metadata(result.metadata_json)

    ingest_cfg = yaml.safe_load((config_root / "preservation/ingest.yaml").read_text())
    db = yaml.safe_load(Path(ingest_cfg["database"]["config_file"]).read_text())["postgres"]

    if result.block_reason and moby_cfg.get("block_on_failure", False):
        status = "blocked"
    else:
        status = None

    conn = psycopg.connect(
        host=db["host"],
        port=int(db.get("port", 5432)),
        dbname=db["database"],
        user=db["user"],
        password=db["password"],
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE game_ingest SET
                moby_game_id = %s,
                moby_title = %s,
                metadata_json = %s,
                metadata_source = %s,
                moby_status = %s,
                block_reason = %s,
                status = COALESCE(%s, status),
                updated_at = NOW()
            WHERE game_key = %s
            """,
            (
                result.moby_game_id,
                result.moby_title,
                json.dumps(result.metadata_json) if result.metadata_json else None,
                result.metadata_source,
                result.status,
                result.block_reason,
                status,
                game_key,
            ),
        )
    conn.commit()
    conn.close()

    print(
        json.dumps(
            {
                "game_key": game_key,
                "moby_status": result.status,
                "block_reason": result.block_reason,
                "moby_game_id": result.moby_game_id,
            },
            indent=2,
        )
    )
    if status == "blocked":
        sys.exit(2)


if __name__ == "__main__":
    main()
