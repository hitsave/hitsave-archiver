#!/usr/bin/env python3
"""Check packages already on Wasabi for damaged files and record the results.

New ingests are checked by build-bagit-package.py (scripts/file_integrity.py). This backfills
packages preserved before that. Per package (ledger status=complete) with images, PDFs,
video/audio or archives that have no integrity result yet:

  - images, PDFs and archives are downloaded to a temp dir, and the bytes must match the bag's
    manifest-sha256.txt on Wasabi (so the preserved copy is what gets checked), else refused;
  - video/audio is read from a presigned URL: ffprobe and the last-15-seconds decode fetch only
    the byte ranges they need, so multi-GB trailers aren't downloaded;
  - with --execute: metadata/inventory.json on Wasabi gets each file's "integrity" record and
    summary.integrity, a PREMIS validation event is added under metadata/premis/, and
    tagmanifest-sha256.txt is uploaded last with their lines. The payload, manifest-sha256.txt and
    bag-info.txt are never touched. The ledger's inventory_json gets the same records.

Damaged files are only recorded. Nothing is deleted, repaired or quarantined.

Dry-run by default: checks files and prints results, writes nothing. Usage (ingest-worker):
  python scripts/check-integrity-wasabi.py [--execute] [--game-key K ...] [--kind press_material]
      [--limit N] [--max-gb 50] [--workers 4] [--report out.json]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import file_integrity as fi  # noqa: E402
from premis_events import iso_now, sha256_hex, stage_event  # noqa: E402

TAGMANIFEST = "tagmanifest-sha256.txt"
MANIFEST = "manifest-sha256.txt"
INVENTORY = "metadata/inventory.json"
PROGRAM = "check-integrity-wasabi.py"
_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def parse_manifest(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            out[parts[1].strip()] = parts[0]
    return out


def update_manifest_line(text: str, relpath: str, digest: str) -> str:
    lines, found = [], False
    for line in text.splitlines(keepends=True):
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1].strip() == relpath:
            lines.append(f"{digest}  {relpath}\n")
            found = True
        else:
            lines.append(line)
    if not found:
        raise ValueError(f"{relpath} not in {TAGMANIFEST}")
    return "".join(lines)


def checkable_files(inventory: dict | None, *, recheck_unchecked: bool = False) -> list[dict]:
    """Inventory entries a checker exists for and that have no result yet (or only "unchecked")."""
    def due(f: dict) -> bool:
        status = (f.get("integrity") or {}).get("status")
        return status is None or (recheck_unchecked and status == "unchecked")
    return [f for f in (inventory or {}).get("files") or []
            if due(f) and fi.kind_for(Path(str(f.get("path") or ""))) is not None]


def merge_integrity(inventory: dict, results: dict[str, fi.IntegrityResult]) -> dict:
    """Copy of *inventory* with results set per path and summary.integrity recounted."""
    out = copy.deepcopy(inventory)
    for entry in out.get("files") or []:
        if entry.get("path") in results:
            entry["integrity"] = results[entry["path"]].record()
    every = {
        e["path"]: fi.IntegrityResult(e["integrity"].get("status", "unchecked"))
        for e in out.get("files") or [] if "integrity" in e
    }
    out.setdefault("summary", {})["integrity"] = fi.summarize(every)
    return out


def _get(s3, bucket: str, key: str) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def _put_verified(s3, bucket: str, key: str, data: bytes, content_type: str) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    if sha256_hex(_get(s3, bucket, key)) != sha256_hex(data):
        raise RuntimeError(f"read-back mismatch for s3://{bucket}/{key}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_package(
    s3,
    *,
    row: dict,
    bucket: str,
    execute: bool,
    workdir: Path,
    presign: Callable[[str, str], str] | None = None,
    recheck_unchecked: bool = False,
) -> dict:
    key = str(row["game_key"])
    prefix = str(row["package_storage_prefix"] or "").strip("/")
    todo = checkable_files(row.get("inventory_json"), recheck_unchecked=recheck_unchecked)
    res: dict = {"game_key": key}
    if not todo:
        res["status"] = "skipped: nothing to check"
        return res

    manifest = parse_manifest(_get(s3, bucket, f"{prefix}/{MANIFEST}").decode("utf-8"))
    results: dict[str, fi.IntegrityResult] = {}
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        for entry in todo:
            rel = str(entry["path"])
            name = Path(rel)
            if fi.kind_for(name) == "media" and presign is not None:
                result = fi.check_file(name, source=presign(bucket, f"{prefix}/{rel}"))
            else:
                local = Path(tmp) / f"f{len(results)}{name.suffix.lower()}"
                s3.download_file(bucket, f"{prefix}/{rel}", str(local))
                if _sha256_file(local) != manifest.get(rel):
                    res["status"] = f"refused: {rel} on Wasabi does not match manifest-sha256.txt"
                    return res
                result = fi.check_file(local)
                local.unlink()
            if result is not None:
                results[rel] = result
                if result.status != "ok":
                    log(f"    {key}: {result.status.upper()} {rel}: {result.detail}")

    res["counts"] = fi.summarize(results)
    res["damaged"] = [{"path": p, "detail": r.detail} for p, r in sorted(results.items()) if r.status == "damaged"]
    if not execute:
        res["status"] = "would record"
        return res

    submission = json.loads(_get(s3, bucket, f"{prefix}/metadata/submission.json"))
    tm_old = _get(s3, bucket, f"{prefix}/{TAGMANIFEST}").decode("utf-8")
    inv_wasabi = json.loads(_get(s3, bucket, f"{prefix}/{INVENTORY}"))
    inv_bytes = (json.dumps(merge_integrity(inv_wasabi, results), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    tm = update_manifest_line(tm_old, INVENTORY, sha256_hex(inv_bytes))
    event_rel, event_bytes, tm = stage_event(tm, fi.validation_event(
        results,
        when=iso_now(),
        program=PROGRAM,
        package_id=str(submission.get("package_id") or key),
        package_uuid=str(submission.get("package_uuid") or ""),
        repository_code=str(submission.get("repository_code") or "hitsave"),
    ), "validation")

    _put_verified(s3, bucket, f"{prefix}/{INVENTORY}", inv_bytes, "application/json")
    _put_verified(s3, bucket, f"{prefix}/{event_rel}", event_bytes, "application/xml")
    _put_verified(s3, bucket, f"{prefix}/{TAGMANIFEST}", tm.encode("utf-8"), "text/plain")
    res["status"] = "recorded"
    res["inventory_json"] = merge_integrity(row["inventory_json"], results)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--game-key", action="append", default=[])
    ap.add_argument("--kind", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-gb", type=float, default=0, help="stop adding packages past this many GB to download")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--workdir", default="/output/tmp")
    ap.add_argument("--report", default="")
    ap.add_argument("--recheck-unchecked", action="store_true", help="check files recorded as unchecked again")
    args = ap.parse_args()
    if missing := fi.missing_tools():
        raise SystemExit(f"missing {', '.join(missing)}: rebuild the ingest-worker image first")

    import psycopg
    from psycopg.types.json import Jsonb

    from preservation_common import wasabi_bucket_for_storage_prefix
    from upload_bag_wasabi import load_wasabi_settings, make_s3_client, resolve_config_path
    from yaml_config import load_yaml

    ingest_path = Path("/config/preservation/ingest.yaml")
    ingest = load_yaml(ingest_path)
    wasabi, _ = load_wasabi_settings(ingest_path)
    creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), ingest_path.parent.parent))
    s3 = make_s3_client(wasabi, creds)
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    conninfo = dict(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                    user=db["user"], password=db["password"])

    sql = ("SELECT game_key, ingest_kind, package_storage_prefix, inventory_json FROM game_ingest "
           "WHERE status = 'complete' AND inventory_json IS NOT NULL AND package_storage_prefix IS NOT NULL")
    params: list = []
    if args.game_key:
        sql += " AND game_key = ANY(%s)"
        params.append(args.game_key)
    if args.kind:
        sql += " AND ingest_kind = %s"
        params.append(args.kind)
    with psycopg.connect(**conninfo) as conn:
        cur = conn.execute(sql + " ORDER BY game_key", params)
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    def todo(row: dict) -> list[dict]:
        return checkable_files(row["inventory_json"], recheck_unchecked=args.recheck_unchecked)

    def download_bytes(row: dict) -> int:
        return sum(int(f.get("size_bytes") or 0) for f in todo(row)
                   if fi.kind_for(Path(f["path"])) != "media")

    work, budget = [], 0
    for row in rows:
        if not todo(row):
            continue
        size = download_bytes(row)
        if args.max_gb and work and budget + size > args.max_gb * 1e9:
            break
        work.append(row)
        budget += size
        if args.limit and len(work) >= args.limit:
            break
    log(f"{len(work)} package(s), {budget / 1e9:.2f} GB to download "
        f"({'EXECUTE' if args.execute else 'dry-run'}, {args.workers} worker(s))")

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    def presign(bucket: str, key: str) -> str:
        return s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=6 * 3600)

    def one(row: dict) -> dict:
        prefix = str(row["package_storage_prefix"]).strip("/")
        bucket = wasabi_bucket_for_storage_prefix(wasabi, prefix, ingest_kind=row["ingest_kind"])
        try:
            res = process_package(s3, row=row, bucket=bucket, execute=args.execute, workdir=workdir, presign=presign,
                                  recheck_unchecked=args.recheck_unchecked)
        except Exception as exc:  # noqa: BLE001
            return {"game_key": row["game_key"], "status": f"error: {exc}"}
        if "inventory_json" in res:
            with psycopg.connect(**conninfo) as conn:
                conn.execute("UPDATE game_ingest SET inventory_json = %s WHERE game_key = %s",
                             (Jsonb(res.pop("inventory_json")), row["game_key"]))
        return res

    results, counts = [], {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(one, r) for r in work]
        for done, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            k = res["status"].split(":")[0]
            counts[k] = counts.get(k, 0) + 1
            c = res.get("counts") or {}
            log(f"[{done}/{len(work)}] {res['status']:<26} {res['game_key']}"
                + (f" checked={c.get('checked')} damaged={c.get('damaged')} unchecked={c.get('unchecked')}" if c else ""))
            results.append(res)
            if args.report:
                Path(args.report).write_text(json.dumps({"counts": counts, "results": results}, indent=2))
    log("Summary: " + json.dumps(counts))
    if any(k.startswith(("error", "refused")) for k in counts):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
