#!/usr/bin/env python3
"""Copy complete packages from Wasabi to S3 Glacier Deep Archive (docs/glacier-copy.md).

A package is due when it has no copy yet, or when its tag manifest on Wasabi differs from the one
recorded at its last copy. Every change to a bag rewrites its tag manifest, so this catches new
catalog snapshots, PREMIS events and rebuilt bags. A dump's disc-contents bag is copied with it.

Each file's SHA-256 is checked against the bag manifest while it streams through (see
glacier_copy_core.py). On success the job upserts package_copy and adds a fixity_run row with
detail "glacier-copy: ..."; a damaged file adds a "fail" row and no copy is recorded. When a
bag's tag manifest changes while it is copied, nothing is recorded and the package stays due.

Report-only unless --execute. Access errors (wrong or expired keys) stop the whole run with
exit code 2 and record nothing. Exit 1 when any package failed.

Usage (ingest-worker; scripts mounted so the image need not contain this file):
    docker compose run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" \\
      --entrypoint python ingest-worker /app/scripts/glacier-copy.py \\
      [--execute] [--max-packages 50] [--max-gb 200] [--kind dump] [--game-key KEY ...]
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
    connect,
    copy_package,
    fingerprint,
    is_fatal,
    prefixes_of,
)

DETAIL_PREFIX = "glacier-copy:"


def due_game_keys(rows: list[dict]) -> list[str]:
    return [r["game_key"] for r in rows if r["copied_fingerprint"] != r["fingerprint"]]


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="Upload and record (default: report only)")
    ap.add_argument("--max-packages", type=int, default=50)
    ap.add_argument("--max-gb", type=float, default=200.0, help="Stop before reading more than this")
    ap.add_argument("--kind", help="Only this ingest_kind (dump, press_material, ...)")
    ap.add_argument("--game-key", action="append", help="Only these packages (repeatable)")
    return ap.parse_args(argv)


def run(argv: list[str], *, ledger, src_s3, dst_s3, dst_bucket: str, bucket_for) -> dict:
    """Copy due packages. bucket_for(ingest_kind, prefix) gives the Wasabi bucket of a package."""
    args = parse_args(argv)
    budget = int(args.max_gb * 1e9)
    spent = 0
    due = 0
    results: list[dict] = []
    try:
        for row in ledger.candidates(kind=args.kind, game_keys=args.game_key):
            if len(results) >= args.max_packages:
                break
            bucket = bucket_for(row["ingest_kind"], row["package_storage_prefix"])
            prefixes = prefixes_of(row)
            try:
                before = fingerprint(src_s3, bucket, prefixes)
            except ClientError as e:
                if is_fatal(e):
                    raise
                results.append({"game_key": row["game_key"], "outcome": "error", "problems": [str(e)]})
                print(f"ERROR {row['game_key']}: cannot read tag manifest: {e}", flush=True)
                continue
            if row["copied_fingerprint"] == before:
                continue
            due += 1
            size = int(row.get("total_bytes") or 0)
            if results and spent + size > budget:
                continue  # try smaller packages that still fit
            if not args.execute:
                results.append({"game_key": row["game_key"], "outcome": "would-copy", "problems": []})
                print(f"DUE  {row['game_key']}: {size / 1e9:.2f} GB in s3://{bucket}/{prefixes[0]}/", flush=True)
                continue
            results.append(copy_one(row, bucket, prefixes, before, ledger, src_s3, dst_s3, dst_bucket))
            spent += results[-1]["bytes_read"]
    except ClientError as e:
        if is_fatal(e):
            raise FatalError(f"stopping: {e}") from e
        raise
    return {
        "packages_due": due,
        "packages_copied": sum(r["outcome"] == "pass" for r in results),
        "failed": sum(r["outcome"] in ("fail", "error") for r in results),
        "skipped_changed": sum(r["outcome"] == "skip" for r in results),
        "gb_read": round(spent / 1e9, 2),
        "executed": bool(args.execute),
    }


def copy_one(row, bucket, prefixes, before, ledger, src_s3, dst_s3, dst_bucket) -> dict:
    t0 = time.time()
    key = row["game_key"]
    uploaded = unchanged = markers = objects = read = total = 0
    problems: list[str] = []
    try:
        for prefix in prefixes:
            r = copy_package(src_s3, bucket, prefix, dst_s3, dst_bucket)
            uploaded += r.files_uploaded
            unchanged += r.files_unchanged
            markers += r.delete_markers
            objects += r.object_count
            total += r.total_bytes
            read += r.bytes_read
            problems += [f"{prefix}/: {p}" for p in r.problems]
        after = fingerprint(src_s3, bucket, prefixes)
    except ClientError as e:
        if is_fatal(e):
            raise
        print(f"ERROR {key}: {e}", flush=True)
        return {"game_key": key, "outcome": "error", "problems": [str(e)], "bytes_read": read}
    dst_prefix = f"{bucket}/{prefixes[0]}/"
    detail = (f"{DETAIL_PREFIX} s3://{dst_bucket}/{dst_prefix} {uploaded} uploaded, {unchanged} unchanged, "
              f"{markers} marked deleted, {read / 1e9:.2f} GB, {time.time() - t0:.0f}s"
              + (": " + "; ".join(problems[:20]) if problems else ""))
    if after != before:
        print(f"SKIP {key}: bag changed during the copy; it stays due", flush=True)
        return {"game_key": key, "outcome": "skip", "problems": problems, "bytes_read": read}
    outcome = "fail" if problems else "pass"
    if not problems:
        ledger.record_copy({"game_ingest_id": row["id"], "location": LOCATION, "bucket": dst_bucket,
                            "prefix": dst_prefix, "object_count": objects, "bytes": total,
                            "tagmanifest_sha256": before})
    ledger.record_fixity(row["id"], outcome, uploaded + unchanged, detail)
    print(f"{outcome.upper():4} {key}: {detail}", flush=True)
    return {"game_key": key, "outcome": outcome, "problems": problems, "bytes_read": read}


class Ledger:
    """package_copy and fixity_run in the ledger database."""

    def __init__(self, conn):
        self.conn = conn

    def candidates(self, kind=None, game_keys=None) -> list[dict]:
        where = ["g.status = 'complete'", "COALESCE(g.package_storage_prefix, '') <> ''"]
        params: list = [LOCATION]
        if kind:
            where.append("g.ingest_kind = %s")
            params.append(kind)
        if game_keys:
            where.append("g.game_key = ANY(%s)")
            params.append(game_keys)
        cur = self.conn.execute(
            f"""
            SELECT g.id, g.game_key, g.ingest_kind, g.package_storage_prefix, g.disc_contents_prefix,
                   COALESCE(g.total_bytes, 0) AS total_bytes,
                   pc.tagmanifest_sha256 AS copied_fingerprint, pc.copied_at
            FROM game_ingest g
            LEFT JOIN package_copy pc ON pc.game_ingest_id = g.id AND pc.location = %s
            WHERE {' AND '.join(where)}
            ORDER BY pc.copied_at NULLS FIRST, g.id
            """,
            params,
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def record_copy(self, row: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO package_copy (game_ingest_id, location, bucket, prefix, object_count, bytes,
                                      tagmanifest_sha256, copied_at)
            VALUES (%(game_ingest_id)s, %(location)s, %(bucket)s, %(prefix)s, %(object_count)s, %(bytes)s,
                    %(tagmanifest_sha256)s, now())
            ON CONFLICT (game_ingest_id, location) DO UPDATE SET
                bucket = EXCLUDED.bucket, prefix = EXCLUDED.prefix, object_count = EXCLUDED.object_count,
                bytes = EXCLUDED.bytes, tagmanifest_sha256 = EXCLUDED.tagmanifest_sha256, copied_at = now()
            """,
            row,
        )
        self.conn.commit()

    def record_fixity(self, game_ingest_id: int, outcome: str, files_checked: int, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO fixity_run (game_ingest_id, outcome, files_checked, detail) VALUES (%s, %s, %s, %s)",
            (game_ingest_id, outcome, files_checked, detail),
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
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
