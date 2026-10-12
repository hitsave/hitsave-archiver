#!/usr/bin/env python3
"""Export preservation-catalog.json from Postgres (complete BagIt ingests)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import psycopg
    import yaml
    from psycopg.rows import dict_row
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from preservation_common import iso_now, load_ingest_config  # noqa: E402
from submission_sources import load_submission_sources_config  # noqa: E402

CATALOG_VERSION = 1


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
        row_factory=dict_row,
    )


def submission_sources_index() -> dict:
    raw = load_submission_sources_config()
    out: dict[str, dict] = {}
    for _name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        sid = str(cfg.get("id") or "")
        if not sid:
            continue
        out[sid] = {
            "submission_date": cfg.get("submission_date"),
            "type": cfg.get("type"),
        }
    return out


def fetch_complete_games(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                game_key,
                source_path,
                submission_source_id,
                package_format,
                package_tar_sha256,
                package_tar_key,
                inventory_json,
                omeka_item_id,
                omeka_media_id,
                moby_title,
                created_at,
                updated_at,
                aip_uploaded_at,
                aip_wasabi_prefix
            FROM game_ingest
            WHERE status = 'complete'
            ORDER BY game_key
            """
        )
        return list(cur.fetchall())


def wasabi_from_row(row: dict) -> tuple[str | None, str | None]:
    key = row.get("package_tar_key")
    if key:
        ingest = load_ingest_config()
        bucket = str((ingest.get("wasabi") or {}).get("bucket") or "")
        return bucket or None, str(key)
    prefix = row.get("aip_wasabi_prefix")
    if prefix and str(prefix).startswith("s3://"):
        rest = str(prefix)[5:]
        if "/" in rest:
            bucket, key = rest.split("/", 1)
            return bucket, key
    return None, key


def build_catalog(rows: list[dict], *, repository_code: str) -> dict:
    games = []
    for row in rows:
        fmt = row.get("package_format") or "bagit"
        inventory = row.get("inventory_json")
        if isinstance(inventory, str):
            inventory = json.loads(inventory)
        bucket, key = wasabi_from_row(row)
        package = {
            "format": fmt,
            "payload_sha256": row.get("package_tar_sha256"),
            "wasabi_bucket": bucket,
            "wasabi_key": key,
        }
        ingested_at = row.get("aip_uploaded_at") or row.get("updated_at")
        if ingested_at is not None:
            ingested_at = ingested_at.isoformat().replace("+00:00", "Z")
        games.append(
            {
                "game_key": row["game_key"],
                "submission_source_id": row.get("submission_source_id"),
                "sip_path": row.get("source_path"),
                "ingested_at": ingested_at,
                "package": package,
                "omeka": {
                    "item_id": row.get("omeka_item_id"),
                    "media_id": row.get("omeka_media_id"),
                },
                "moby_title": row.get("moby_title"),
                "inventory": inventory,
            }
        )
    return {
        "catalog_version": CATALOG_VERSION,
        "exported_at": iso_now(),
        "repository_code": repository_code,
        "submission_sources": submission_sources_index(),
        "games": games,
    }


def main() -> None:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("preservation-catalog.json")
    ingest = load_ingest_config()
    db_path = Path(ingest["database"]["config_file"])
    db_cfg = load_yaml(db_path)["postgres"]
    repository_code = str((ingest.get("preservation") or {}).get("repository_code") or "hitsave")

    with pg_connect(db_cfg) as conn:
        rows = fetch_complete_games(conn)

    catalog = build_catalog(rows, repository_code=repository_code)
    out_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path} ({len(catalog['games'])} complete games)")


if __name__ == "__main__":
    main()
