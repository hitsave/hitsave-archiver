#!/usr/bin/env python3
"""MOVE ledger-complete Wasabi intake packages into processed/{source_prefix}/{folder}/.

Uses Wasabi HTTP MOVE (X-Wasabi-Prefix), same as wasabi_rename.wasabi_move_prefix.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import psycopg
except ImportError:
    psycopg = None  # type: ignore[assignment,misc]

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from move_incoming_redump_packages import (  # noqa: E402
    dest_prefix_has_objects,
    make_move_client,
    resolve_config_path,
)
from upload_bag_wasabi import load_wasabi_settings  # noqa: E402
from wasabi_rename import wasabi_move_prefix  # noqa: E402
from yaml_config import load_yaml  # noqa: E402
from ready_source import MISMATCH, check_source, describe, require_ready_root  # noqa: E402

DEST_ROOT = "processed"


def list_object_keys(client, bucket: str, prefix: str) -> list[str]:
    root = prefix.strip("/") + "/"
    keys: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=root):
        for obj in page.get("Contents") or []:
            key = str(obj["Key"])
            if key == root or key.endswith("/"):
                continue
            keys.append(key)
    return sorted(keys)


def pg_connect(db_cfg: dict):
    if psycopg is None:
        raise RuntimeError("psycopg is required")
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def ledger_complete_for_slug(slug: str, *, ingest_config_path: Path) -> bool:
    ingest = load_yaml(ingest_config_path)
    db_path = Path(ingest["database"]["config_file"])
    db_cfg = load_yaml(db_path)["postgres"]
    slug_sql = slug.replace("'", "''")
    q = (
        "SELECT status FROM game_ingest WHERE ingest_kind='dump' "
        f"AND (source_path LIKE '%/{slug_sql}' OR source_path LIKE '%/{slug_sql}/%') "
        "ORDER BY (status = 'complete') DESC LIMIT 1"
    )
    with pg_connect(db_cfg) as conn:
        row = conn.execute(q).fetchone()
    return bool(row and str(row[0]).strip() == "complete")


def load_packages_from_poll_json(path: Path) -> list[dict]:
    import json

    raw = path.read_text(encoding="utf-8")
    start = raw.find("{")
    data = json.loads(raw[start:])
    return list(data.get("packages") or [])


def main() -> None:
    ready_root = require_ready_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--intake-config",
        type=Path,
        default=Path("/tmp/wasabi-redump-intake.yaml"),
        help="wasabi_redump_intake YAML (bucket + credentials paths)",
    )
    parser.add_argument(
        "--poll-json",
        type=Path,
        required=True,
        help="Poll output JSON (intake poll-wasabi --once)",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--overwrite", action="store_true", help="Allow MOVE into existing dest prefix")
    args = parser.parse_args()

    doc = load_yaml(args.intake_config)
    cfg = doc.get("wasabi_redump_intake") or {}
    if not cfg:
        raise SystemExit(f"No wasabi_redump_intake in {args.intake_config}")

    config_root = args.intake_config.resolve().parent.parent
    ingest_path = resolve_config_path(str(cfg["ingest_config_file"]), config_root)
    wasabi, _ = load_wasabi_settings(ingest_path)
    cred_path = resolve_config_path(str(cfg["credentials_file"]), config_root)
    if not cred_path.is_file():
        fallback = Path("/config/secrets/wasabi-credentials.yaml")
        if fallback.is_file():
            cred_path = fallback
    creds = load_yaml(cred_path)
    client = make_move_client(wasabi, creds, cfg)
    bucket = str(cfg["source"]["bucket"])

    packages = load_packages_from_poll_json(args.poll_json)
    planned: list[tuple[str, str, str]] = []
    skipped: list[str] = []

    for pkg in packages:
        src_root = (pkg.get("source_prefix") or "").strip().strip("/")
        folder = (pkg.get("package_folder_name") or "").strip().strip("/")
        slug = (pkg.get("ready_slug") or "").strip()
        if not src_root or not folder or not slug:
            skipped.append(f"malformed: {pkg!r}")
            continue
        if not ledger_complete_for_slug(slug, ingest_config_path=ingest_path):
            skipped.append(f"not ledger-complete: {src_root}/{folder} ({slug})")
            continue
        # Slug collision guard: the ledger row matched by slug must come from THIS source.
        state, rec = check_source(ready_root / slug, wasabi_prefix=f"{src_root}/{folder}")
        if state == MISMATCH:
            skipped.append(
                f"COLLISION: {src_root}/{folder} ({slug}) — ready/{slug} came from {describe(rec)}; "
                "this source was NOT preserved under that slug"
            )
            continue
        src = f"{src_root}/{folder}"
        dest = f"{DEST_ROOT}/{src}"
        if src.startswith(f"{DEST_ROOT}/"):
            skipped.append(f"already under processed: {src}")
            continue
        planned.append((src, dest, slug))

    print(f"Planned MOVE: {len(planned)} package(s) → {DEST_ROOT}/…")
    for src, dest, slug in planned:
        print(f"  {src}/ -> {dest}/  ({slug})")
    if skipped:
        print(f"\nSkipped ({len(skipped)}):")
        for line in skipped:
            print(f"  {line}")

    stats = {"moved": 0, "objects": 0, "errors": 0, "skipped_dest": 0}
    for src, dest, slug in planned:
        print(f"\n{'DRY-RUN' if not args.execute else 'MOVE'} s3://{bucket}/{src}/ -> {dest}/")
        src_keys = list_object_keys(client, bucket, src)
        if not src_keys:
            print("  skip: no objects at source (already under processed/)")
            continue
        if args.execute and not args.overwrite and dest_prefix_has_objects(client, bucket, dest):
            stats["skipped_dest"] += 1
            print(f"  skip: destination already has objects")
            continue
        if not args.execute:
            resp = client.list_objects_v2(Bucket=bucket, Prefix=src + "/", MaxKeys=500)
            n = len(resp.get("Contents") or [])
            stats["moved"] += 1
            stats["objects"] += n
            print(f"  would move ~{n} object key(s)")
            continue
        try:
            result = wasabi_move_prefix(
                client,
                bucket=bucket,
                source_prefix=src,
                destination_prefix=dest,
                overwrite=args.overwrite,
                quiet=True,
            )
        except Exception as e:
            stats["errors"] += 1
            print(f"  ERROR: {e}", file=sys.stderr)
            continue
        if result.get("errors"):
            stats["errors"] += len(result["errors"])
            for err in result["errors"]:
                print(f"  move error: {err}", file=sys.stderr)
        count = int(result.get("move_count") or 0)
        stats["objects"] += count
        stats["moved"] += 1
        print(f"  moved {count} object key(s)")

    print("\nSummary:", stats)
    if not args.execute:
        print("Dry-run only. Re-run with --execute to apply.")
    if stats["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
