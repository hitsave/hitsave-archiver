#!/usr/bin/env python3
"""Move complete dump bags on Wasabi to dumps/{sort_letter}/… and refresh ledger + Omeka staff URI."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import psycopg  # noqa: E402

from preservation_common import wasabi_bucket_for_storage_prefix  # noqa: E402
from upload_bag_wasabi import load_wasabi_settings, make_s3_client  # noqa: E402
from wasabi_rename import wasabi_move_prefix  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

_SORT_LETTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ") | {"0-9"}


def dump_prefix_with_sort_letter(package_storage_prefix: str, sort_letter: str) -> str:
    """Insert sort letter before package_id: dumps/{system}/{category}/{letter}/{package_id}."""
    parts = package_storage_prefix.strip("/").split("/")
    if not parts or parts[0] != "dumps":
        return package_storage_prefix.strip("/")
    letter = (sort_letter or "X").strip("/") or "X"

    # Target: dumps/System/Category/L/package_id
    if len(parts) == 5 and parts[1] != "Others" and parts[3] in _SORT_LETTERS:
        return "/".join(parts)
    # Target: dumps/Others/L/package_id
    if len(parts) == 4 and parts[1] == "Others" and parts[2] in _SORT_LETTERS:
        return "/".join(parts)

    # Legacy wrong layout: dumps/L/System/Category/package_id
    if len(parts) == 5 and parts[1] in _SORT_LETTERS:
        return f"dumps/{parts[2]}/{parts[3]}/{letter}/{parts[4]}"

    # Legacy: dumps/Others/package_id
    if len(parts) == 3 and parts[1] == "Others":
        return f"dumps/Others/{letter}/{parts[2]}"

    # Legacy redump: dumps/System/Category/package_id
    if len(parts) == 4:
        return f"dumps/{parts[1]}/{parts[2]}/{letter}/{parts[3]}"

    return "/".join(parts)


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Perform Wasabi MOVE and ledger updates")
    parser.add_argument(
        "--register-omeka",
        action="store_true",
        help="After migrate, run apply_dump_catalog_omeka for each updated game_key",
    )
    args = parser.parse_args()

    ingest_path = Path("/config/preservation/ingest.yaml")
    if not ingest_path.is_file():
        ingest_path = REPO_ROOT / "config" / "preservation" / "ingest.yaml"
    ingest_cfg = load_yaml(ingest_path)
    wasabi_cfg, _ = load_wasabi_settings(ingest_path)
    cred_path = Path(ingest_path).resolve().parent.parent / str(
        wasabi_cfg["credentials_file"]
    )
    creds = load_yaml(cred_path)
    s3 = make_s3_client(wasabi_cfg, creds)

    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    conn = pg_connect(db_cfg)
    rows: list[tuple] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, package_storage_prefix, sort_letter, omeka_item_id
                FROM game_ingest
                WHERE ingest_kind = 'dump'
                  AND status = 'complete'
                  AND package_storage_prefix IS NOT NULL
                  AND package_storage_prefix <> ''
                ORDER BY game_key
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    planned: list[dict] = []
    for game_key, old_prefix, sort_letter, omeka_item_id in rows:
        old_p = str(old_prefix).strip("/")
        new_p = dump_prefix_with_sort_letter(old_p, str(sort_letter or "X"))
        if old_p == new_p:
            continue
        bucket = wasabi_bucket_for_storage_prefix(wasabi_cfg, old_p, ingest_kind="dump")
        planned.append(
            {
                "game_key": game_key,
                "bucket": bucket,
                "old_prefix": old_p,
                "new_prefix": new_p,
                "sort_letter": sort_letter,
                "omeka_item_id": omeka_item_id,
            }
        )

    print(json.dumps({"dry_run": not args.execute, "packages_to_relocate": len(planned)}, indent=2))
    for item in planned:
        print(
            f"  {item['game_key']}: s3://{item['bucket']}/{item['old_prefix']}/"
            f" -> s3://{item['bucket']}/{item['new_prefix']}/"
        )

    if not args.execute or not planned:
        return

    updated_keys: list[str] = []
    for item in planned:
        bucket = item["bucket"]
        old_p = item["old_prefix"]
        new_p = item["new_prefix"]
        print(f"MOVE {old_p}/ -> {new_p}/")
        wasabi_move_prefix(
            s3,
            bucket=bucket,
            source_prefix=old_p,
            destination_prefix=new_p,
            overwrite=False,
        )
        aip_uri = f"s3://{bucket}/{new_p}/"
        conn = pg_connect(db_cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE game_ingest
                    SET package_storage_prefix = %s,
                        aip_wasabi_prefix = %s,
                        updated_at = NOW()
                    WHERE game_key = %s
                    """,
                    (new_p, aip_uri, item["game_key"]),
                )
            conn.commit()
        finally:
            conn.close()
        updated_keys.append(str(item["game_key"]))

    print(f"Ledger updated for {len(updated_keys)} package(s).")

    if args.register_omeka and updated_keys:
        print(json.dumps({"omeka_refresh_game_keys": updated_keys}, indent=2))


if __name__ == "__main__":
    main()
