#!/usr/bin/env python3
"""READ-ONLY: prove Wasabi dump bags hold exactly the ingested bytes.

For every file in each complete dump bag (payload manifest + tag manifest):
  1. stream the LOCAL bag file once: SHA-256 (vs bag manifest = ingest-time digest)
     and per-part MD5s (64 MiB parts, matching upload_bag_wasabi.S3_TRANSFER);
  2. HEAD the Wasabi object: compare ContentLength and the S3 ETag
     (plain MD5 for single-part uploads, md5(part md5s)-N for multipart).
Local SHA-256 == manifest AND remote ETag == local ETag  =>  Wasabi == ingested bytes.

No writes to Wasabi, the ledger, or /output. Progress: <out>/progress.jsonl (resumable);
final report: <out>/summary.json.

Usage (inside ingest-worker, which mounts /output and the ledger config):
    docker compose run --rm -T --no-deps -v "$PWD/.generated:/reports" \
      --entrypoint python ingest-worker -u /app/scripts/verify_wasabi_etags.py \
      /reports/etag-verify [workers=4]
"""
import hashlib
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, "/app/scripts")

PART = 64 * 1024 * 1024        # upload_bag_wasabi.S3_TRANSFER chunksize/threshold
READ = 8 * 1024 * 1024
if len(sys.argv) < 2:
    raise SystemExit("usage: verify_wasabi_etags.py <out_dir> [workers]")
OUT = Path(sys.argv[1])
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 4


def parse_manifest(path: Path) -> dict[str, str]:
    out = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, rel = line.split(None, 1)
            out[rel.strip()] = digest.lower()
    return out


def hash_local(path: str) -> dict:
    sha = hashlib.sha256()
    whole_md5 = hashlib.md5()
    part_digests: list[bytes] = []
    part = hashlib.md5()
    in_part = 0
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(READ)
            if not chunk:
                break
            size += len(chunk)
            sha.update(chunk)
            whole_md5.update(chunk)
            view = memoryview(chunk)
            while view:
                take = min(len(view), PART - in_part)
                part.update(view[:take])
                in_part += take
                view = view[take:]
                if in_part == PART:
                    part_digests.append(part.digest())
                    part = hashlib.md5()
                    in_part = 0
    if in_part:
        part_digests.append(part.digest())
    multipart_etag = None
    if part_digests:
        multipart_etag = f"{hashlib.md5(b''.join(part_digests)).hexdigest()}-{len(part_digests)}"
    return {
        "size": size,
        "sha256": sha.hexdigest(),
        "md5": whole_md5.hexdigest(),
        "multipart_etag": multipart_etag,
        "parts": len(part_digests),
    }


def job(task: dict) -> dict:
    t0 = time.time()
    res = dict(task)
    try:
        loc = hash_local(task["local_path"])
    except Exception as e:  # noqa: BLE001
        res.update(status="LOCAL_ERROR", detail=str(e))
        return res
    res["local_size"] = loc["size"]
    res["local_sha256"] = loc["sha256"]
    problems = []
    if task.get("expected_sha256") and loc["sha256"] != task["expected_sha256"]:
        problems.append("local sha256 != bag manifest (local copy changed since ingest)")
    remote_etag = task["remote_etag"]
    if task["remote_size"] != loc["size"]:
        problems.append(f"size mismatch remote={task['remote_size']} local={loc['size']}")
    if "-" in remote_etag:
        n = int(remote_etag.rsplit("-", 1)[1])
        if n != loc["parts"]:
            res["etag_check"] = "unverifiable"
            problems.append(f"multipart part count {n} != expected {loc['parts']} at 64 MiB")
        elif remote_etag != loc["multipart_etag"]:
            problems.append("ETag mismatch (multipart)")
        else:
            res["etag_check"] = "multipart_ok"
    else:
        if remote_etag != loc["md5"]:
            problems.append("ETag mismatch (single-part md5)")
        else:
            res["etag_check"] = "md5_ok"
    res["status"] = "OK" if not problems else "MISMATCH"
    if problems:
        res["detail"] = "; ".join(problems)
    res["seconds"] = round(time.time() - t0, 1)
    return res


def build_tasks(done: set[str]) -> list[dict]:
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
    with psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                         user=db["user"], password=db["password"]) as conn:
        rows = conn.execute(
            "SELECT game_key, package_storage_prefix, aip_bag_path FROM game_ingest "
            "WHERE status='complete' AND ingest_kind='dump' ORDER BY game_key").fetchall()

    tasks = []
    for game_key, prefix, bag_path in rows:
        prefix = prefix.strip("/")
        bucket = wasabi_bucket_for_storage_prefix(wasabi, prefix, ingest_kind="dump")
        bag = Path(bag_path)
        payload = parse_manifest(bag / "manifest-sha256.txt")
        tags = parse_manifest(bag / "tagmanifest-sha256.txt")
        files = {**payload, **tags}
        if not files:
            tasks.append({"game_key": game_key, "rel": "", "status": "NO_LOCAL_BAG",
                          "detail": f"no manifests under {bag}", "prebuilt": True})
            continue
        for rel, digest in files.items():
            key = f"{prefix}/{rel}"
            ident = f"{bucket}/{key}"
            if ident in done:
                continue
            base = {"game_key": game_key, "rel": rel, "bucket": bucket, "key": key,
                    "local_path": str(bag / rel), "expected_sha256": digest}
            try:
                head = s3.head_object(Bucket=bucket, Key=key)
            except Exception as e:  # noqa: BLE001
                tasks.append({**base, "status": "REMOTE_MISSING", "detail": str(e), "prebuilt": True})
                continue
            base["remote_size"] = int(head["ContentLength"])
            base["remote_etag"] = str(head["ETag"]).strip('"').lower()
            tasks.append(base)
    return tasks


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    progress = OUT / "progress.jsonl"
    done = set()
    if progress.is_file():
        for line in progress.read_text().splitlines():
            r = json.loads(line)
            if r.get("status") in ("OK", "MISMATCH") and r.get("bucket"):
                done.add(f"{r['bucket']}/{r['key']}")
    tasks = build_tasks(done)
    todo = [t for t in tasks if not t.get("prebuilt")]
    total_bytes = sum(t["remote_size"] for t in todo)
    print(f"resume: {len(done)} done; {len(todo)} files to hash ({total_bytes/1e9:.1f} GB); "
          f"{len(tasks)-len(todo)} prebuilt problems", flush=True)
    t0 = time.time()
    hashed = 0
    with progress.open("a") as fh:
        for t in tasks:
            if t.get("prebuilt"):
                print(f"{t['status']} {t['game_key']} {t['rel']}: {t['detail']}", flush=True)
                fh.write(json.dumps(t) + "\n")
        fh.flush()
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            futs = [pool.submit(job, t) for t in sorted(todo, key=lambda t: -t["remote_size"])]
            for i, fut in enumerate(as_completed(futs), 1):
                r = fut.result()
                fh.write(json.dumps(r) + "\n")
                fh.flush()
                hashed += r.get("remote_size", 0)
                if r["status"] != "OK" or r.get("remote_size", 0) > PART or i % 200 == 0:
                    rate = hashed / max(1, time.time() - t0) / 1e6
                    print(f"[{i}/{len(todo)} {hashed/1e9:.0f}/{total_bytes/1e9:.0f} GB "
                          f"{rate:.0f} MB/s] {r['status']} {r['game_key']} {r['rel']}"
                          + (f" :: {r.get('detail')}" if r['status'] != 'OK' else ""), flush=True)

    # Final summary over the whole progress file (latest result per object wins).
    latest = {}
    for line in progress.read_text().splitlines():
        r = json.loads(line)
        latest[f"{r.get('bucket')}/{r.get('key') or r.get('game_key')}"] = r
    by_status: dict[str, int] = {}
    for r in latest.values():
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    problems = [r for r in latest.values() if r["status"] != "OK"]
    summary = {
        "objects": len(latest),
        "packages": len({r["game_key"] for r in latest.values()}),
        "bytes_verified": sum(r.get("remote_size", 0) for r in latest.values() if r["status"] == "OK"),
        "by_status": by_status,
        "etag_checks": {k: sum(1 for r in latest.values() if r.get("etag_check") == k)
                        for k in ("multipart_ok", "md5_ok", "unverifiable")},
        "problems": problems,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print("SUMMARY " + json.dumps({k: v for k, v in summary.items() if k != "problems"}), flush=True)


if __name__ == "__main__":
    main()
