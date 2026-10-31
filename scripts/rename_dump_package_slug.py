#!/usr/bin/env python3
"""Rename a dump BagIt prefix on Wasabi when title_slug improves (same UUID, new slug segment)."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import psycopg
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from audit_dump_title_slugs import catalog_title_for_row, host_ready_root  # noqa: E402
from package_identity import parse_package_id, title_slug  # noqa: E402
from upload_bag_wasabi import load_wasabi_settings, make_s3_client  # noqa: E402
from wasabi_rename import wasabi_move_prefix_verified  # noqa: E402
from preservation_common import wasabi_bucket_for_storage_prefix  # noqa: E402
from sync_dump_bag_metadata_from_ledger import sync_bag_from_ledger  # noqa: E402
from yaml_config import load_yaml  # noqa: E402


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
    parser.add_argument("--game-key", required=True, help="Current ledger game_key / package_id")
    parser.add_argument("--execute", action="store_true", help="Perform Wasabi MOVE and ledger UPDATE")
    parser.add_argument(
        "--title",
        help="Override catalog title for new slug (default: read from ready handoff YAML)",
    )
    parser.add_argument(
        "--skip-bag-metadata-sync",
        action="store_true",
        help="Do not patch Wasabi bag-info/submission.json after MOVE (default: sync when --execute)",
    )
    args = parser.parse_args()

    ingest_path = Path("/config/preservation/ingest.yaml")
    ingest = load_yaml(ingest_path)
    db_cfg = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    wasabi_cfg, _ = load_wasabi_settings(ingest_path)
    from upload_bag_wasabi import resolve_config_path  # noqa: E402

    creds = load_yaml(resolve_config_path(str(wasabi_cfg["credentials_file"]), ingest_path.parent.parent))
    s3 = make_s3_client(wasabi_cfg, creds)

    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, source_path, package_storage_prefix, sort_letter, package_id
                FROM game_ingest
                WHERE game_key = %s AND ingest_kind = 'dump' AND status = 'complete'
                """,
                (args.game_key,),
            )
            row = cur.fetchone()
        if not row:
            raise SystemExit(f"No complete dump row for game_key={args.game_key!r}")

    game_key, source_path, old_prefix, sort_letter, package_id = row
    old_slug, uid = parse_package_id(game_key)
    title = (args.title or "").strip() or catalog_title_for_row(str(source_path or ""), host_ready_root())
    if not title:
        raise SystemExit("Could not resolve catalog title; pass --title or ensure ready handoff YAML exists")

    new_slug = title_slug(title)
    new_game_key = f"{new_slug}-{uid}"
    old_p = str(old_prefix or "").strip("/")
    if not old_p:
        raise SystemExit("Ledger missing package_storage_prefix")

    parts = old_p.split("/")
    if parts[-1] != game_key:
        raise SystemExit(f"Prefix tail {parts[-1]!r} != game_key {game_key!r}")
    new_p = "/".join(parts[:-1] + [new_game_key])
    bucket = wasabi_bucket_for_storage_prefix(wasabi_cfg, old_p, ingest_kind="dump")

    plan = {
        "old_game_key": game_key,
        "new_game_key": new_game_key,
        "title": title,
        "old_slug": old_slug,
        "new_slug": new_slug,
        "bucket": bucket,
        "old_prefix": old_p,
        "new_prefix": new_p,
        "sort_letter": sort_letter,
    }
    print(plan)

    if old_p == new_p:
        print("No change needed.")
        return
    if not args.execute:
        print("Dry run only; pass --execute to MOVE and update ledger.")
        return

    print(f"MOVE s3://{bucket}/{old_p}/ -> s3://{bucket}/{new_p}/")
    # Raises (before any ledger change) unless every object landed at new_p.
    wasabi_move_prefix_verified(
        s3,
        bucket=bucket,
        source_prefix=old_p,
        destination_prefix=new_p,
        overwrite=False,
    )
    aip_uri = f"s3://{bucket}/{new_p}/"
    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE game_ingest
                SET game_key = %s,
                    title_slug = %s,
                    package_id = %s,
                    package_storage_prefix = %s,
                    aip_wasabi_prefix = %s,
                    updated_at = NOW()
                WHERE game_key = %s
                """,
                (new_game_key, new_slug, new_game_key, new_p, aip_uri, game_key),
            )
        conn.commit()
    print(f"Ledger updated: {game_key} -> {new_game_key}")

    if args.skip_bag_metadata_sync:
        print("Skipped bag metadata sync (--skip-bag-metadata-sync).")
        return

    ledger_row = {
        "game_key": new_game_key,
        "title_slug": new_slug,
        "package_storage_prefix": new_p,
        "aip_wasabi_prefix": aip_uri,
        "sort_letter": sort_letter,
    }
    print("Syncing BagIt tag metadata from ledger…")
    sync_plan = sync_bag_from_ledger(
        s3,
        bucket=bucket,
        storage_prefix=new_p,
        ledger_row=ledger_row,
        execute=True,
    )
    print(f"Bag metadata synced: {sync_plan.get('files')}")


if __name__ == "__main__":
    main()
