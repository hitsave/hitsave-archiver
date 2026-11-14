#!/usr/bin/env python3
"""Check the Glacier Deep Archive copy of every package without restoring anything.

For each package with an up-to-date copy (package_copy row whose tag-manifest digests still
match Wasabi), every file in the bag's manifests must exist in the copy with the same size and
the same x-amz-meta-sha256. A HEAD request returns both for archived objects, so a full check
costs about $0.05 and reads nothing back from Glacier. The check also reports files that are
current in the copy but gone from Wasabi ("not marked deleted").

Results go to fixity_run with detail "glacier: ...", and a pass sets package_copy.verified_at,
unless --no-record. A copy that is out of date is skipped: the nightly copy job brings it up to
date first. Access errors stop the run with exit code 2. Exit 1 when any package fails.

Usage (ingest-worker; scripts mounted so the image need not contain this file):
    docker compose run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" \\
      --entrypoint python ingest-worker /app/scripts/glacier-copy-check.py [--no-record] [--game-key KEY ...]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from botocore.exceptions import ClientError  # noqa: E402

from glacier_copy_core import (  # noqa: E402
    LOCATION,
    FatalError,
    list_source,
    connect,
    expected_digests,
    fingerprint,
    is_fatal,
    prefixes_of,
)

DETAIL_PREFIX = "glacier:"


def check_prefix(src_s3, bucket: str, prefix: str, dst_s3, dst_bucket: str) -> tuple[list[str], int]:
    """(problems, files_checked) for one bag: compare Wasabi's manifests and listing with the copy."""
    root = prefix.strip("/") + "/"
    dst_root = f"{bucket}/{root}"
    expected, _ = expected_digests(src_s3, bucket, prefix)
    source = list_source(src_s3, bucket, root)
    copy: dict[str, int] = {}
    for page in dst_s3.get_paginator("list_objects_v2").paginate(Bucket=dst_bucket, Prefix=dst_root):
        for o in page.get("Contents") or []:
            copy[str(o["Key"])[len(dst_root):]] = int(o["Size"])
    problems: list[str] = []
    checked = 0
    for rel in sorted(set(expected) | set(source)):
        if rel not in copy:
            problems.append(f"missing in the copy: {rel}")
            continue
        head = dst_s3.head_object(Bucket=dst_bucket, Key=dst_root + rel)
        checked += 1
        if rel in source and int(head["ContentLength"]) != source[rel]:
            problems.append(f"size differs: {rel}")
        elif rel in expected and (head.get("Metadata") or {}).get("sha256") != expected[rel]:
            problems.append(f"SHA-256 differs: {rel}")
    for rel in sorted(copy):
        if rel not in source:
            problems.append(f"not marked deleted: {rel}")
    return problems, checked


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-key", action="append", help="Only these packages (repeatable)")
    ap.add_argument("--no-record", action="store_true", help="Do not write fixity_run or verified_at")
    return ap.parse_args(argv)


def run(argv: list[str], *, ledger, src_s3, dst_s3, dst_bucket: str, bucket_for) -> dict:
    args = parse_args(argv)
    counts = {"passed": 0, "failed": 0, "skipped_out_of_date": 0, "errors": 0}
    try:
        for row in ledger.copies(game_keys=args.game_key):
            key = row["game_key"]
            bucket = bucket_for(row["ingest_kind"], row["package_storage_prefix"])
            prefixes = prefixes_of(row)
            t0 = time.time()
            try:
                if fingerprint(src_s3, bucket, prefixes) != row["tagmanifest_sha256"]:
                    counts["skipped_out_of_date"] += 1
                    print(f"SKIP {key}: copy is out of date", flush=True)
                    continue
                problems: list[str] = []
                checked = 0
                for prefix in prefixes:
                    p, n = check_prefix(src_s3, bucket, prefix, dst_s3, dst_bucket)
                    problems += [f"{prefix}/: {x}" for x in p]
                    checked += n
            except ClientError as e:
                if is_fatal(e):
                    raise
                counts["errors"] += 1
                print(f"ERROR {key}: {e}", flush=True)
                continue
            outcome = "fail" if problems else "pass"
            counts["passed" if outcome == "pass" else "failed"] += 1
            detail = (f"{DETAIL_PREFIX} s3://{dst_bucket}/{bucket}/{prefixes[0]}/ {checked} files, "
                      f"{time.time() - t0:.0f}s" + (": " + "; ".join(problems[:20]) if problems else ""))
            if not args.no_record:
                ledger.record_fixity(row["id"], outcome, checked, detail)
                if outcome == "pass":
                    ledger.mark_verified(row["id"])
            print(f"{outcome.upper():4} {key}: {detail}", flush=True)
    except ClientError as e:
        if is_fatal(e):
            raise FatalError(f"stopping: {e}") from e
        raise
    return {**counts, "recorded": not args.no_record}


class Ledger:
    def __init__(self, conn):
        self.conn = conn

    def copies(self, game_keys=None) -> list[dict]:
        where = ["pc.location = %s", "g.status = 'complete'"]
        params: list = [LOCATION]
        if game_keys:
            where.append("g.game_key = ANY(%s)")
            params.append(game_keys)
        cur = self.conn.execute(
            f"""
            SELECT g.id, g.game_key, g.ingest_kind, g.package_storage_prefix, g.disc_contents_prefix,
                   pc.tagmanifest_sha256
            FROM package_copy pc JOIN game_ingest g ON g.id = pc.game_ingest_id
            WHERE {' AND '.join(where)}
            ORDER BY pc.verified_at NULLS FIRST, g.id
            """,
            params,
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def record_fixity(self, game_ingest_id: int, outcome: str, files_checked: int, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO fixity_run (game_ingest_id, outcome, files_checked, detail) VALUES (%s, %s, %s, %s)",
            (game_ingest_id, outcome, files_checked, detail),
        )
        self.conn.commit()

    def mark_verified(self, game_ingest_id: int) -> None:
        self.conn.execute(
            "UPDATE package_copy SET verified_at = now() WHERE game_ingest_id = %s AND location = %s",
            (game_ingest_id, LOCATION),
        )
        self.conn.commit()


def main() -> int:
    conn, src, dst, aws, bucket_for = connect()
    try:
        summary = run(sys.argv[1:], ledger=Ledger(conn), src_s3=src, dst_s3=dst,
                      dst_bucket=str(aws["bucket"]), bucket_for=bucket_for)
    except FatalError as e:
        print(f"FATAL {e}", file=sys.stderr, flush=True)
        return 2
    finally:
        conn.close()
    print(json.dumps(summary))
    return 1 if summary["failed"] or summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
