#!/usr/bin/env python3
"""Get files back from the Glacier Deep Archive copy (docs/glacier-copy.md). Never writes to Wasabi.

Files in Deep Archive must be restored before they can be downloaded. A Bulk restore takes up to
48 hours and costs $0.0025/GB; Standard takes up to 12 hours and costs $0.02/GB. A restored file
stays readable for 7 days. Downloads from AWS cost $0.09/GB after the first 100 GB each month.

    glacier-restore.py request --game-key KEY [--file data/disc.iso] [--tier Bulk|Standard]
    glacier-restore.py status  --game-key KEY [--file ...]
    glacier-restore.py fetch   --game-key KEY [--file ...] --to DIR

Use --prefix hitsave-dumps/dumps/... instead of --game-key when the ledger is not available.
fetch downloads the restored files into DIR (never overwriting a file), checks each SHA-256
against the digest the bag manifest gave when it was copied (x-amz-meta-sha256), and exits 1 on
any problem. Putting files back on Wasabi is a separate, manual step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

from botocore.exceptions import ClientError  # noqa: E402

from glacier_copy_core import LOCATION, FatalError, is_fatal  # noqa: E402

RESTORE_DAYS = 7
CHUNK = 8 * 2**20


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("request", "status", "fetch"):
        p = sub.add_parser(name)
        which = p.add_mutually_exclusive_group(required=True)
        which.add_argument("--game-key", help="Package from the ledger's package_copy row")
        which.add_argument("--prefix", help="Copy prefix, e.g. hitsave-dumps/dumps/sony-psx/...")
        p.add_argument("--file", action="append", help="Only this file, relative to the bag (repeatable)")
        if name == "request":
            p.add_argument("--tier", choices=("Bulk", "Standard"), default="Bulk")
        if name == "fetch":
            p.add_argument("--to", required=True, help="Empty or new folder to download into")
    return ap.parse_args(argv)


def list_files(dst_s3, bucket: str, prefix: str, only: list[str] | None) -> list[str]:
    rels = []
    for page in dst_s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents") or []:
            rels.append(str(o["Key"])[len(prefix):])
    return sorted(r for r in rels if not only or r in only)


def is_ready(head: dict) -> bool:
    if head.get("StorageClass") not in ("DEEP_ARCHIVE", "GLACIER"):
        return True
    return 'ongoing-request="false"' in str(head.get("Restore") or "")


def safe_target(root: Path, rel: str) -> Path | None:
    parts = PurePosixPath(rel).parts
    if not parts or rel.startswith("/") or any(p in ("..", "") for p in parts):
        return None
    return root.joinpath(*parts)


def request(dst_s3, bucket, prefix, rels, tier) -> dict:
    requested = already = 0
    for rel in rels:
        try:
            dst_s3.restore_object(Bucket=bucket, Key=prefix + rel,
                                  RestoreRequest={"Days": RESTORE_DAYS, "GlacierJobParameters": {"Tier": tier}})
            requested += 1
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "RestoreAlreadyInProgress":
                raise
            already += 1
    return {"requested": requested, "already_requested": already, "tier": tier}


def status(dst_s3, bucket, prefix, rels) -> dict:
    ready, waiting = [], []
    for rel in rels:
        (ready if is_ready(dst_s3.head_object(Bucket=bucket, Key=prefix + rel)) else waiting).append(rel)
    return {"ready": ready, "waiting": waiting}


def fetch(dst_s3, bucket, prefix, rels, to: Path) -> dict:
    problems: list[str] = []
    fetched = not_ready = 0
    for rel in rels:
        target = safe_target(to, rel)
        if target is None:
            problems.append(f"unsafe path: {rel}")
            continue
        if target.exists():
            problems.append(f"already exists: {rel}")
            continue
        head = dst_s3.head_object(Bucket=bucket, Key=prefix + rel)
        if not is_ready(head):
            not_ready += 1
            continue
        want = (head.get("Metadata") or {}).get("sha256")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        sha = hashlib.sha256()
        body = dst_s3.get_object(Bucket=bucket, Key=prefix + rel)["Body"]
        with partial.open("xb") as fh:
            while chunk := body.read(CHUNK):
                sha.update(chunk)
                fh.write(chunk)
        partial.rename(target)
        fetched += 1
        if want is None:
            problems.append(f"no recorded SHA-256: {rel}")
        elif sha.hexdigest() != want:
            problems.append(f"SHA-256 differs from the bag manifest: {rel}")
    return {"fetched": fetched, "not_ready": not_ready, "problems": problems}


def run(argv: list[str], *, dst_s3, dst_bucket: str, prefix_for) -> dict:
    """prefix_for(game_key) gives the copy prefix (<wasabi bucket>/<package prefix>/)."""
    args = parse_args(argv)
    prefix = (args.prefix.strip("/") + "/") if args.prefix else prefix_for(args.game_key)
    try:
        rels = list_files(dst_s3, dst_bucket, prefix, args.file)
        if args.command == "request":
            return request(dst_s3, dst_bucket, prefix, rels, args.tier)
        if args.command == "status":
            return status(dst_s3, dst_bucket, prefix, rels)
        return fetch(dst_s3, dst_bucket, prefix, rels, Path(args.to))
    except ClientError as e:
        if is_fatal(e):
            raise FatalError(f"stopping: {e}") from e
        raise


def ledger_prefix(game_key: str) -> str:
    import psycopg

    from glacier_copy_core import INGEST_CONFIG
    from yaml_config import load_yaml

    db = load_yaml(Path(load_yaml(INGEST_CONFIG)["database"]["config_file"]))["postgres"]
    with psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                         user=db["user"], password=db["password"]) as conn:
        row = conn.execute(
            "SELECT pc.prefix FROM package_copy pc JOIN game_ingest g ON g.id = pc.game_ingest_id "
            "WHERE g.game_key = %s AND pc.location = %s", (game_key, LOCATION),
        ).fetchone()
    if row is None:
        raise SystemExit(f"no copy recorded for {game_key}")
    return str(row[0])


def main() -> int:
    from glacier_copy_core import AWS_CONFIG, INGEST_CONFIG, make_aws_client
    from upload_bag_wasabi import resolve_config_path
    from yaml_config import load_yaml

    aws = load_yaml(AWS_CONFIG)
    creds = load_yaml(resolve_config_path(str(aws["credentials_file"]), INGEST_CONFIG.parent.parent))
    try:
        out = run(sys.argv[1:], dst_s3=make_aws_client(aws, creds), dst_bucket=str(aws["bucket"]),
                  prefix_for=ledger_prefix)
    except FatalError as e:
        print(f"FATAL {e}", file=sys.stderr, flush=True)
        return 2
    print(json.dumps(out, indent=2))
    return 1 if out.get("problems") else 0


if __name__ == "__main__":
    sys.exit(main())
