#!/usr/bin/env python3
"""Clear Omeka IDs (and optional Moby) on ledger rows for a batch re-run."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import psycopg
    import yaml
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]


def config_root() -> Path:
    container = Path("/config/preservation/ingest.yaml")
    if container.is_file():
        return Path("/config")
    return ROOT / "config"


def main() -> None:
    batch_key = sys.argv[1] if len(sys.argv) > 1 else "repcopies-t-25"
    root = config_root()
    ingest_cfg = yaml.safe_load((root / "preservation/ingest.yaml").read_text())
    db = yaml.safe_load(Path(ingest_cfg["database"]["config_file"]).read_text())["postgres"]
    like = f"{batch_key}%"
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
                status = 'pending',
                omeka_item_id = NULL,
                omeka_media_id = NULL,
                moby_game_id = NULL,
                moby_title = NULL,
                metadata_json = NULL,
                metadata_source = NULL,
                moby_status = NULL,
                block_reason = NULL,
                updated_at = NOW()
            WHERE game_key LIKE %s
            """,
            (like,),
        )
        n = cur.rowcount
    conn.commit()
    conn.close()
    print(f"Reset {n} ledger rows matching game_key prefix {batch_key}")


if __name__ == "__main__":
    main()
