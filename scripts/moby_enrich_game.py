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
from ingest_kind import moby_enrich_enabled, moby_omeka_profile_for_game_cfg  # noqa: E402
from moby_resolve import (  # noqa: E402
    apply_moby_omeka_profile_to_metadata,
    finalize_moby_metadata,
    load_moby_config,
    resolve_for_game,
    uses_moby_catalog_data,
)
from package_ledger import resolve_package_identity  # noqa: E402
from preservation_common import load_ingest_config, resolve_game_config  # noqa: E402


def ledger_game_key_for_bagit_source(conn, source: Path) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT game_key FROM game_ingest
            WHERE source_path = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (str(source.resolve()),),
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def main() -> None:
    game_cfg_path = Path(sys.argv[1])
    raw = yaml.safe_load(game_cfg_path.read_text())
    game_cfg = resolve_game_config(raw, agent_version="moby_enrich_game.py")
    source = Path(game_cfg["source_game_folder"]).resolve()
    game_key = str(game_cfg.get("game_key") or "")

    ingest_cfg = load_ingest_config()
    press_root = Path(ingest_cfg["paths"]["press_material_root"])
    db_cfg = yaml.safe_load(Path(ingest_cfg["database"]["config_file"]).read_text())["postgres"]
    title = str(game_cfg.get("omeka_item_title") or source.name)
    with psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    ) as conn:
        ledger_key = ledger_game_key_for_bagit_source(conn, source)
        if ledger_key:
            game_key = ledger_key
        else:
            identity = resolve_package_identity(
                conn,
                source=source,
                press_material_root=press_root,
                omeka_item_title=title,
            )
            game_key = identity.package_id
    folder_name = Path(game_cfg["source_game_folder"]).name
    display_title = game_cfg.get("omeka_item_title")

    config_root = Path("/config") if Path("/config/mobygames.yaml").is_file() else REPO_ROOT / "config"
    moby_cfg = load_moby_config(config_root).get("mobygames") or {}
    omeka_profile = moby_omeka_profile_for_game_cfg(game_cfg)
    if not moby_enrich_enabled(game_cfg):
        print(
            json.dumps(
                {
                    "game_key": game_key,
                    "moby_status": "skipped",
                    "ingest_kind": game_cfg.get("ingest_kind"),
                    "reason": "moby_enrich disabled for ingest kind",
                },
                indent=2,
            )
        )
        return
    result = resolve_for_game(
        config_root=config_root,
        folder_name=folder_name,
        display_title=display_title,
        moby_game_id=game_cfg.get("moby_game_id"),
        moby_search_title=game_cfg.get("moby_search_title"),
    )
    if result.metadata_json and uses_moby_catalog_data(result.metadata_json):
        finalize_moby_metadata(result.metadata_json)
        apply_moby_omeka_profile_to_metadata(result.metadata_json, omeka_profile, moby_cfg)

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
        if cur.rowcount != 1:
            raise RuntimeError(
                f"Moby ledger UPDATE matched {cur.rowcount} rows for game_key={game_key!r} "
                f"(source={source})"
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
