#!/usr/bin/env python3
"""Free /tank: delete local working copies of packages whose Wasabi copy is proven good.

Local bags (/output/bag/...) and intake SIPs (intake ready/<slug>/) are scratch space,
not a backup. Wasabi is the preservation copy (see docs/storage-retention.md). Before any
deletion, this proves the Wasabi copy equals the ingest-time bytes WITHOUT downloading payload:

  1. every file in the ledger's ingest-time inventory is hashed locally: SHA-256 must equal the
     inventory digest (the local copy is still the ingested bytes);
  2. HEAD on each Wasabi object: size and S3 ETag (MD5 / multipart MD5) must equal the local file
     (so Wasabi == local == ingested bytes);
  3. the Wasabi payload manifest must list exactly the inventory with the same digests; each
     Wasabi tag file is downloaded (small) and checked against the Wasabi tagmanifest; no stray
     payload objects;
  4. the most recent Wasabi-side fixity run (wasabi-fixity-check.py), if any, must not be a fail;
  5. nothing local would be lost: every payload file in the local bag / ready dir is in the
     inventory (anything else was never preserved, so the package is refused).

Then it deletes the local bag directory and the payload in ready/<slug>/, KEEPING the handoff
files (hitsave-submission.yaml, game.ingest.yaml): the slug-collision guard (ready_source.py)
reads them, and without them a new package with the same slug would be skipped as "already
preserved". A PRUNED.txt marker is written there. Originals under /data/press-material are
never touched. Only status 'complete' rows older than --min-age-days are considered.

Dry run by default (read-only everywhere). With --execute it deletes and records a fixity_run
row (detail "wasabi-etag: ...") per verified package. Exit 1 if any package failed verification.

Usage (ingest-worker; see windmill_ops/wmill/f/hitsave/prune_local_working_copies.sh):
    prune-local-working-copies.py [--execute] [--min-age-days 14] [--max-packages 50]
        [--max-gb 200] [--kind dump] [--game-key KEY ...] [--ready-root /data/intake-ready]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from s3_etag import etag_matches, hash_file  # noqa: E402

KEEP_IN_READY = frozenset({"hitsave-submission.yaml", "game.ingest.yaml"})
MARKER = "PRUNED.txt"
DETAIL_PREFIX = "wasabi-etag:"
BAG_ROOT = Path("/output/bag")
# ledger source_path for intake SIPs, as seen by ingest-worker
LEDGER_READY_ROOT = "/data/gdrive-intake/ready"
TAG_FILE_MAX_BYTES = 64 * 1024 * 1024


@dataclass
class Candidate:
    gid: int
    game_key: str
    kind: str
    prefix: str
    bucket: str
    inventory: dict
    bag_dir: Path | None = None
    ready_dir: Path | None = None
    problems: list[str] = field(default_factory=list)


# --- path safety ----------------------------------------------------------------------------

def contained_dir(path: str | Path | None, root: Path) -> Path | None:
    """`path` if it is an existing real directory strictly below `root` (no symlinks), else None."""
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute() or ".." in p.parts:
        return None
    if p.is_symlink() or not p.is_dir():
        return None
    try:
        real = p.resolve(strict=True)
        root_real = root.resolve(strict=True)
    except OSError:
        return None
    if real == root_real or root_real not in real.parents:
        return None
    return real


def ready_dir_for(source_path: str | None, ready_root: Path) -> Path | None:
    """Map a ledger source_path /data/gdrive-intake/ready/<slug> to <ready_root>/<slug>."""
    if not source_path:
        return None
    src = Path(source_path)
    if src.parent != Path(LEDGER_READY_ROOT) or src.name in ("", ".", ".."):
        return None
    d = ready_root / src.name
    return contained_dir(d, ready_root) if d.exists() else None


def local_payload_files(base: Path, *, skip_names: frozenset[str] = frozenset()) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if Path(dirpath) == base and name in skip_names:
                continue
            out.append(p)
    return out


# --- verification ---------------------------------------------------------------------------

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


def inventory_files(inventory: dict | None) -> dict[str, tuple[str, int]]:
    out = {}
    for f in (inventory or {}).get("files") or []:
        rel = str(f.get("path") or "")
        out[rel] = (str(f.get("sha256") or "").lower(), int(f.get("size_bytes") if f.get("size_bytes") is not None else -1))
    return out


def ready_rel_to_bag_rel(ready_dir: Path, path: Path) -> str:
    """ready/<slug>/x/y -> data/<slug>/x/y (how build-bagit-package lays out an intake SIP)."""
    return f"data/{ready_dir.name}/{path.relative_to(ready_dir).as_posix()}"


def verify(s3, c: Candidate) -> tuple[list[str], int]:
    """Return (problems, local bytes hashed). Empty problems => safe to delete local copies."""
    problems: list[str] = []
    inv = inventory_files(c.inventory)
    if not inv:
        return ["ledger inventory is empty"], 0
    if any(not d or s < 0 for d, s in inv.values()):
        return ["ledger inventory lacks sha256/size for some files"], 0

    # 5. nothing local that was never preserved
    if c.bag_dir:
        for p in local_payload_files(c.bag_dir / "data"):
            rel = p.relative_to(c.bag_dir).as_posix()
            if rel not in inv:
                problems.append(f"local bag file not in ingest inventory: {rel}")
    if c.ready_dir:
        for p in local_payload_files(c.ready_dir, skip_names=KEEP_IN_READY | {MARKER}):
            rel = ready_rel_to_bag_rel(c.ready_dir, p)
            if rel not in inv:
                problems.append(f"ready file not in ingest inventory: {rel}")
            elif p.stat().st_size != inv[rel][1]:
                problems.append(f"ready file size differs from inventory: {rel}")
    if problems:
        return problems, 0

    # 3. Wasabi manifests / tag files / strays
    root = c.prefix.strip("/") + "/"
    try:
        objs = list_objects(s3, c.bucket, root)
        manifest_bytes = s3.get_object(Bucket=c.bucket, Key=root + "manifest-sha256.txt")["Body"].read()
        tag_bytes = s3.get_object(Bucket=c.bucket, Key=root + "tagmanifest-sha256.txt")["Body"].read()
    except Exception as e:  # noqa: BLE001
        return [f"cannot read Wasabi bag: {e}"], 0
    manifest = parse_manifest(manifest_bytes.decode("utf-8"))
    tagmanifest = parse_manifest(tag_bytes.decode("utf-8"))
    if set(manifest) != set(inv):
        problems.append(
            f"Wasabi manifest ({len(manifest)} files) does not list exactly the ingest inventory ({len(inv)})"
        )
    for rel, digest in manifest.items():
        if rel in inv and inv[rel][0] != digest:
            problems.append(f"Wasabi manifest digest differs from ingest inventory: {rel}")
    for rel in objs:
        if rel.startswith("data/") and rel not in manifest:
            problems.append(f"stray Wasabi payload object: {rel}")
    if "manifest-sha256.txt" not in tagmanifest:
        problems.append("Wasabi tagmanifest does not cover manifest-sha256.txt")
    for rel, digest in tagmanifest.items():
        size = objs.get(rel)
        if size is None:
            problems.append(f"missing Wasabi tag file: {rel}")
            continue
        if size > TAG_FILE_MAX_BYTES:
            problems.append(f"Wasabi tag file unexpectedly large: {rel}")
            continue
        data = manifest_bytes if rel == "manifest-sha256.txt" else s3.get_object(
            Bucket=c.bucket, Key=root + rel)["Body"].read()
        if hashlib.sha256(data).hexdigest() != digest:
            problems.append(f"Wasabi tag file SHA-256 mismatch: {rel}")
    if problems:
        return problems, 0

    # 1 + 2. local bytes == ingest inventory, Wasabi object == local bytes
    hashed = 0
    for rel, (digest, size) in sorted(inv.items()):
        local = None
        if c.bag_dir and (c.bag_dir / rel).is_file():
            local = c.bag_dir / rel
        elif c.ready_dir:
            prefix = f"data/{c.ready_dir.name}/"
            if rel.startswith(prefix) and (c.ready_dir / rel[len(prefix):]).is_file():
                local = c.ready_dir / rel[len(prefix):]
        if local is None:
            problems.append(f"no local copy to verify against: {rel}")
            continue
        if objs.get(rel) != size:
            problems.append(f"Wasabi object missing or wrong size: {rel}")
            continue
        h = hash_file(local)
        hashed += h.size
        if h.size != size or h.sha256 != digest:
            problems.append(f"local file no longer matches ingest SHA-256: {rel}")
            continue
        head = s3.head_object(Bucket=c.bucket, Key=root + rel)
        if int(head["ContentLength"]) != size:
            problems.append(f"Wasabi HEAD size mismatch: {rel}")
            continue
        ok, detail = etag_matches(h, str(head.get("ETag") or ""))
        if not ok:
            problems.append(f"Wasabi ETag does not match local bytes: {rel} ({detail})")
    return problems, hashed


# --- deletion -------------------------------------------------------------------------------

def prune(c: Candidate, *, now: str) -> list[str]:
    """Delete the local copies of a verified package. Returns what was removed."""
    removed = []
    if c.ready_dir:
        for child in sorted(c.ready_dir.iterdir()):
            if child.name in KEEP_IN_READY or child.name == MARKER:
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed.append(str(child))
        (c.ready_dir / MARKER).write_text(
            f"Payload removed {now} by prune-local-working-copies.py.\n"
            f"Preserved copy: s3://{c.bucket}/{c.prefix.strip('/')}/ (verified by SHA-256 + ETag).\n"
            f"Handoff files are kept on purpose: the slug-collision guard reads them.\n",
            encoding="utf-8",
        )
    if c.bag_dir:
        shutil.rmtree(c.bag_dir)
        removed.append(str(c.bag_dir))
    return removed


# --- main -----------------------------------------------------------------------------------

def last_wasabi_fixity_failed(conn, gid: int) -> bool:
    row = conn.execute(
        "SELECT outcome FROM fixity_run WHERE game_ingest_id = %s AND detail LIKE 'wasabi:%%' "
        "ORDER BY checked_at DESC LIMIT 1",
        (gid,),
    ).fetchone()
    return bool(row) and row[0] != "pass"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="Delete (default: dry run, read-only)")
    ap.add_argument("--min-age-days", type=float, default=14.0,
                    help="Only packages uploaded at least this long ago (time for manual post-steps)")
    ap.add_argument("--max-packages", type=int, default=50)
    ap.add_argument("--max-gb", type=float, default=200.0, help="Stop before hashing more local GB")
    ap.add_argument("--kind", help="Only this ingest_kind")
    ap.add_argument("--game-key", action="append", help="Only these packages (repeatable)")
    ap.add_argument("--ready-root", type=Path, default=Path("/data/intake-ready"),
                    help="Writable mount of intake ready/ (ingest-worker's own mount is read-only)")
    args = ap.parse_args()

    import psycopg

    from preservation_common import wasabi_bucket_for_storage_prefix
    from upload_bag_wasabi import load_wasabi_settings, make_s3_client, resolve_config_path
    from yaml_config import load_yaml

    ingest_path = Path("/config/preservation/ingest.yaml")
    ingest = load_yaml(ingest_path)
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    wasabi, _ = load_wasabi_settings(ingest_path)
    creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), ingest_path.parent.parent))
    s3 = make_s3_client(wasabi, creds)
    conn = psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                           user=db["user"], password=db["password"])

    ready_root = args.ready_root
    if not ready_root.is_dir():
        if args.kind in (None, "dump"):
            raise SystemExit(f"--ready-root {ready_root} is not mounted; refusing (dump SIPs live there)")
        ready_root = Path("/nonexistent")
    if not BAG_ROOT.is_dir():
        raise SystemExit(f"{BAG_ROOT} is not mounted")

    where = [
        "status = 'complete'",
        "COALESCE(package_storage_prefix, '') <> ''",
        "aip_uploaded_at IS NOT NULL",
        "aip_uploaded_at < now() - make_interval(secs => %s)",
    ]
    params: list = [args.min_age_days * 86400]
    if args.kind:
        where.append("ingest_kind = %s")
        params.append(args.kind)
    if args.game_key:
        where.append("game_key = ANY(%s)")
        params.append(args.game_key)
    rows = conn.execute(
        f"SELECT id, game_key, ingest_kind, package_storage_prefix, inventory_json, aip_bag_path, "
        f"source_path FROM game_ingest WHERE {' AND '.join(where)} ORDER BY aip_uploaded_at",
        params,
    ).fetchall()

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    budget = int(args.max_gb * 1e9)
    hashed_total = 0
    results = []
    for gid, game_key, kind, prefix, inv, bag_path, source_path in rows:
        if len(results) >= args.max_packages:
            break
        bag_dir = contained_dir(bag_path, BAG_ROOT)
        ready_dir = ready_dir_for(source_path, ready_root)
        if ready_dir and not local_payload_files(ready_dir, skip_names=KEEP_IN_READY | {MARKER}):
            ready_dir = None  # already pruned
        if not bag_dir and not ready_dir:
            continue  # nothing local left
        if isinstance(inv, str):
            inv = json.loads(inv)
        size = sum(s for _, s in inventory_files(inv).values() if s > 0)
        if results and hashed_total + size > budget:
            continue  # try smaller packages that still fit
        c = Candidate(gid, game_key, kind, prefix,
                      wasabi_bucket_for_storage_prefix(wasabi, prefix, ingest_kind=kind),
                      inv or {}, bag_dir, ready_dir)
        t0 = time.time()
        if last_wasabi_fixity_failed(conn, gid):
            problems, hashed = ["most recent Wasabi fixity run failed"], 0
        else:
            problems, hashed = verify(s3, c)
        hashed_total += hashed
        outcome = "fail" if problems else "pass"
        removed: list[str] = []
        if args.execute:
            conn.execute(
                "INSERT INTO fixity_run (game_ingest_id, outcome, files_checked, detail) VALUES (%s, %s, %s, %s)",
                (gid, outcome, len(inventory_files(inv)),
                 f"{DETAIL_PREFIX} s3://{c.bucket}/{prefix.strip('/')}/ vs local {hashed / 1e9:.2f} GB, "
                 f"{time.time() - t0:.0f}s" + (": " + "; ".join(problems[:20]) if problems else "")),
            )
            conn.commit()
            if not problems:
                removed = prune(c, now=now)
        action = ("DELETED" if removed else "WOULD DELETE") if not problems else "KEEP"
        print(f"{outcome.upper():4} {action:12} {game_key}: bag={bag_dir or '-'} ready={ready_dir or '-'} "
              f"{size / 1e9:.2f} GB" + (": " + "; ".join(problems[:5]) if problems else ""), flush=True)
        results.append({"game_key": game_key, "outcome": outcome, "bytes": size, "problems": problems,
                        "removed": removed})

    conn.close()
    passed = [r for r in results if r["outcome"] == "pass"]
    print(json.dumps({
        "mode": "execute" if args.execute else "dry-run",
        "candidates_checked": len(results),
        "verified": len(passed),
        "failed": len(results) - len(passed),
        # payload size per package; disk freed is up to 2x for dumps (bag + ready/ copy)
        "payload_gb_pruned" if args.execute else "payload_gb_would_prune": round(sum(r["bytes"] for r in passed) / 1e9, 2),
        "gb_hashed": round(hashed_total / 1e9, 2),
    }))
    if len(passed) != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
