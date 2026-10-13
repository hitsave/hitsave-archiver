#!/usr/bin/env python3
"""Ledger lookups for batch resume (BagIt package_id vs expand slug in game YAML)."""
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
from preservation_common import load_ingest_config, resolve_game_config  # noqa: E402


def config_root() -> Path:
    if Path("/config/preservation/ingest.yaml").is_file():
        return Path("/config")
    return REPO_ROOT / "config"


def source_path_from_game_config(cfg_path: Path) -> str:
    raw = yaml.safe_load(cfg_path.read_text()) or {}
    game_cfg = resolve_game_config(raw)
    return str(Path(game_cfg["source_game_folder"]).resolve())


def pg_connect():
    ingest = load_ingest_config()
    db_path = Path(ingest["database"]["config_file"])
    if not db_path.is_file():
        db_path = config_root() / "preservation/database.yaml"
    db_cfg = yaml.safe_load(db_path.read_text())["postgres"]
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def ledger_row_for_config(cfg_path: Path) -> dict | None:
    source = source_path_from_game_config(cfg_path)
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, status, dip_path, omeka_item_id, omeka_media_id,
                       moby_game_id, metadata_json IS NOT NULL AS has_metadata
                FROM game_ingest
                WHERE source_path = %s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (source,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "game_key": row[0],
        "status": row[1],
        "dip_path": row[2],
        "omeka_item_id": row[3],
        "omeka_media_id": row[4],
        "moby_game_id": row[5],
        "has_metadata": bool(row[6]),
        "source_path": source,
    }


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("Usage: batch_ledger.py status|row <game.yaml>")
    cmd = sys.argv[1]
    cfg = Path(sys.argv[2])
    row = ledger_row_for_config(cfg)
    if cmd == "status":
        print(row["status"] if row else "")
        return
    if cmd == "row":
        print(json.dumps(row or {}, indent=2))
        return
    if cmd == "game_key":
        print(row["game_key"] if row else "")
        return
    if cmd == "needs_moby":
        if not row or row["status"] != "complete":
            print("0")
            return
        # Re-run when Moby never persisted (ingest used wrong ledger key before package_id fix).
        needs = row["moby_game_id"] is None
        print("1" if needs else "0")
        return
    raise SystemExit(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
