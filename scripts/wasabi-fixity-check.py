#!/usr/bin/env python3
"""Recurring fixity check of the preservation copy ON WASABI (read-only on Wasabi).

fixity-check.py only validates the local bag under /output; nothing re-read the Wasabi
copy. This streams each object of a sample of complete packages back from Wasabi and checks:

  * every manifest-sha256 / tagmanifest-sha256 entry exists and its SHA-256 matches;
  * payload digests in the Wasabi manifest equal the ingest-time inventory in the ledger
    (catches a manifest that was regenerated over changed bytes);
  * no stray payload objects (not in the manifest) and no untracked tag files.

Packages are picked least-recently-checked first (by previous wasabi fixity_run rows),
until --max-packages or the --max-gb read budget is reached. Results are recorded in
fixity_run with detail prefixed "wasabi:" unless --no-record. Exit 1 if any package fails.

Usage (ingest-worker; scripts mounted so the image need not contain this file):
    docker compose run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" \\
      --entrypoint python ingest-worker /app/scripts/wasabi-fixity-check.py \\
      [--max-packages 10] [--max-gb 50] [--kind dump] [--game-key KEY] [--no-record]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg  # noqa: E402

from preservation_common import wasabi_bucket_for_storage_prefix  # noqa: E402
from upload_bag_wasabi import load_wasabi_settings, make_s3_client, resolve_config_path  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

DETAIL_PREFIX = "wasabi:"
STREAM_CHUNK = 8 * 1024 * 1024


def parse_manifest(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip():
            digest, rel = line.split(None, 1)
            out[rel.strip()] = digest.lower()
    return out


def list_objects(s3, bucket: str, root: str) -> dict[str, int]:
    objs: dict[str, int] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=root):
        for o in page.get("Contents") or []:
            key = str(o["Key"])
            if not key.endswith("/"):
                objs[key[len(root):]] = int(o["Size"])
    return objs


def sha256_object(s3, bucket: str, key: str) -> str:
    digest = hashlib.sha256()
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    for chunk in body.iter_chunks(STREAM_CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


def check_package(s3, bucket: str, prefix: str, inventory: dict | None) -> tuple[list[str], int, int]:
    """Return (problems, files_checked, bytes_read) for one unpacked bag on Wasabi."""
    root = prefix.strip("/") + "/"
    objs = list_objects(s3, bucket, root)
    problems: list[str] = []
    try:
        manifest = parse_manifest(
            s3.get_object(Bucket=bucket, Key=root + "manifest-sha256.txt")["Body"].read().decode()
        )
        tagmanifest = parse_manifest(
            s3.get_object(Bucket=bucket, Key=root + "tagmanifest-sha256.txt")["Body"].read().decode()
        )
    except Exception as e:  # noqa: BLE001
        return [f"cannot read manifests: {e}"], 0, 0

    inv = {
        str(f.get("path")): str(f.get("sha256") or "").lower()
        for f in (inventory or {}).get("files") or []
    }
    for rel, digest in manifest.items():
        if rel in inv and inv[rel] != digest:
            problems.append(f"manifest digest differs from ingest inventory: {rel}")
    for rel in inv:
        if rel not in manifest:
            problems.append(f"ingest inventory file missing from manifest: {rel}")
    for rel in objs:
        if rel.startswith("data/") and rel not in manifest:
            problems.append(f"stray payload object: {rel}")
        elif not rel.startswith("data/") and rel not in tagmanifest and rel != "tagmanifest-sha256.txt":
            problems.append(f"untracked tag file: {rel}")

    checked = 0
    read = 0
    for rel, digest in {**manifest, **tagmanifest}.items():
        if rel not in objs:
            problems.append(f"missing object: {rel}")
            continue
        actual = sha256_object(s3, bucket, root + rel)
        read += objs[rel]
        checked += 1
        if actual != digest:
            problems.append(f"SHA-256 mismatch: {rel}")
    return problems, checked, read


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-packages", type=int, default=10)
    ap.add_argument("--max-gb", type=float, default=50.0, help="Stop before reading more than this")
    ap.add_argument("--kind", help="Only this ingest_kind (dump, press_material, …)")
    ap.add_argument("--game-key", action="append", help="Check these packages (repeatable)")
    ap.add_argument("--no-record", action="store_true", help="Do not write fixity_run rows")
    args = ap.parse_args()

    ingest_path = Path("/config/preservation/ingest.yaml")
    ingest = load_yaml(ingest_path)
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    wasabi, _ = load_wasabi_settings(ingest_path)
    creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), ingest_path.parent.parent))
    s3 = make_s3_client(wasabi, creds)
    conn = psycopg.connect(
        host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
        user=db["user"], password=db["password"],
    )

    where = ["g.status = 'complete'", "COALESCE(g.package_storage_prefix, '') <> ''"]
    params: list = []
    if args.kind:
        where.append("g.ingest_kind = %s")
        params.append(args.kind)
    if args.game_key:
        where.append("g.game_key = ANY(%s)")
        params.append(args.game_key)
    rows = conn.execute(
        f"""
        SELECT g.id, g.game_key, g.ingest_kind, g.package_storage_prefix, g.inventory_json,
               COALESCE(g.total_bytes, 0)
        FROM game_ingest g
        LEFT JOIN LATERAL (
            SELECT max(checked_at) AS last_checked FROM fixity_run f
            WHERE f.game_ingest_id = g.id AND f.detail LIKE '{DETAIL_PREFIX}%%'
        ) lc ON true
        WHERE {' AND '.join(where)}
        ORDER BY lc.last_checked NULLS FIRST, random()
        """,
        params,
    ).fetchall()

    budget = int(args.max_gb * 1e9)
    spent = 0
    results = []
    for gid, game_key, kind, prefix, inv, total_bytes in rows:
        if len(results) >= args.max_packages:
            break
        if results and spent + int(total_bytes) > budget:
            continue  # try smaller packages that still fit
        if isinstance(inv, str):
            inv = json.loads(inv)
        bucket = wasabi_bucket_for_storage_prefix(wasabi, prefix, ingest_kind=kind)
        t0 = time.time()
        problems, checked, read = check_package(s3, bucket, prefix, inv)
        spent += read
        outcome = "fail" if problems else "pass"
        detail = (
            f"{DETAIL_PREFIX} s3://{bucket}/{prefix.strip('/')}/ {checked} files, "
            f"{read / 1e9:.2f} GB, {time.time() - t0:.0f}s"
            + (": " + "; ".join(problems[:20]) if problems else "")
        )
        if not args.no_record:
            conn.execute(
                "INSERT INTO fixity_run (game_ingest_id, outcome, files_checked, detail) VALUES (%s, %s, %s, %s)",
                (gid, outcome, checked, detail),
            )
            conn.commit()
        results.append({"game_key": game_key, "outcome": outcome, "files": checked, "problems": problems})
        print(f"{outcome.upper():4} {game_key}: {detail}", flush=True)

    conn.close()
    failed = [r for r in results if r["outcome"] == "fail"]
    print(json.dumps({
        "packages_checked": len(results),
        "failed": len(failed),
        "gb_read": round(spent / 1e9, 2),
        "recorded": not args.no_record,
    }))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
