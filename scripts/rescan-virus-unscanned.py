#!/usr/bin/env python3
"""
Virus-scan the files that ingest could not scan (larger than the old 512 MiB ClamAV limit), now
that clamav_full_scan covers every byte of any file, and record it as a NEW PREMIS event.

Per package (ledger status=complete with a payload file > 512 MiB and no rescan event yet):
  - each such file is scanned from the local bag copy with clamav_full_scan.scan_path;
  - the SHA-256 of the scanned bytes must equal that file's line in the bag's manifest-sha256.txt
    on Wasabi (proves the preserved bytes were scanned), otherwise the package is refused;
  - a new tag file metadata/premis/virus-check-rescan-<date>.xml is written (outcome success only
    if every byte was scanned and nothing found), referencing the ingest-time event it supplements;
  - tagmanifest-sha256.txt gains one line for it. The ingest-time PREMIS event, the payload,
    manifest-sha256.txt and bag-info.txt are not touched. Uploads are verified by read-back; the
    local bag gets the same files when its tagmanifest is identical to Wasabi's.
A detection aborts that package (nothing written) and is reported loudly.

Dry-run by default (scans nothing). Usage (inside ingest-worker, clamd reachable):
  python scripts/rescan-virus-unscanned.py [--execute] [--workers 4] [--limit N] [--game-key K]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from clamav_full_scan import MalwareFound, scan_path  # noqa: E402
from premis_virus_check import (  # noqa: E402
    PREMIS_NS,
    VirusScanReport,
    build_virus_check_premis_xml,
    clamav_version_info,
    iso_now,
)

LEGACY_LIMIT = 536_870_912  # what ingest skipped before clamav_full_scan
ORIG_PREMIS = "metadata/premis/virus-check-event.xml"
TAGMANIFEST = "tagmanifest-sha256.txt"
MANIFEST = "manifest-sha256.txt"
P = f"{{{PREMIS_NS}}}"
_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_manifest(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            out[parts[1].strip()] = parts[0].lower()
    return out


def targets(inventory: dict | None) -> list[str]:
    return sorted(
        str(f["path"]) for f in (inventory or {}).get("files") or []
        if int(f.get("size_bytes") or 0) > LEGACY_LIMIT
    )


def rescan_relpath(on: str) -> str:
    return f"metadata/premis/virus-check-rescan-{on}.xml"


def build_rescan_premis(report: VirusScanReport, *, package_id: str, package_uuid: str,
                        repository_code: str, original_event: str) -> str:
    xml = build_virus_check_premis_xml(
        report, package_id=package_id, package_uuid=package_uuid,
        repository_code=repository_code, linking_agent="rescan-virus-unscanned.py",
    )
    root = ET.fromstring(xml.encode("utf-8"))
    detail = root.find(f"{P}eventDetailInformation/{P}eventDetail")
    detail.text = f'{detail.text}; supplementsEvent="{original_event}"'
    note = root.find(f"{P}eventOutcomeInformation/{P}eventOutcomeDetailNote")
    note.text = (
        f"Rescan of the {report.files_scanned + report.files_not_scanned} file(s) that the ingest-time "
        f"virus check (event {original_event}) could not scan. {note.text}"
    )
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def add_tagmanifest_line(tagmanifest: str, relpath: str, digest: str) -> str:
    if any(line.split(None, 1)[-1].strip() == relpath for line in tagmanifest.splitlines() if line.strip()):
        raise ValueError(f"{relpath} already in {TAGMANIFEST}")
    body = tagmanifest if tagmanifest.endswith("\n") or not tagmanifest else tagmanifest + "\n"
    return f"{body}{digest}  {relpath}\n"


def _get(s3, bucket, key) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def _put_verified(s3, bucket, key, data: bytes, content_type: str) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    if sha256_hex(_get(s3, bucket, key)) != sha256_hex(data):
        raise RuntimeError(f"read-back mismatch for s3://{bucket}/{key}")


def process(clamd_factory, s3, *, row: dict, bucket: str, prefix: str, execute: bool, on: str) -> dict:
    key = row["game_key"]
    files = targets(row["inventory_json"])
    res = {"game_key": key, "files": files}
    tm_old = _get(s3, bucket, f"{prefix}/{TAGMANIFEST}").decode("utf-8")
    if re.search(r"metadata/premis/virus-check-rescan-[0-9-]+\.xml", tm_old):
        res["status"] = "skipped: rescan event already present"
        return res
    if not execute:
        res["status"] = "would rescan"
        return res

    manifest = parse_manifest(_get(s3, bucket, f"{prefix}/{MANIFEST}").decode("utf-8"))
    orig = ET.fromstring(_get(s3, bucket, f"{prefix}/{ORIG_PREMIS}"))
    original_event = orig.find(f"{P}eventIdentifier/{P}eventIdentifierValue").text
    submission = json.loads(_get(s3, bucket, f"{prefix}/metadata/submission.json"))
    bag = Path(str(row.get("aip_bag_path") or ""))

    clamd = clamd_factory()
    program, version, defs = clamav_version_info(clamd)
    scans, not_full = [], []
    for rel in files:
        local = bag / rel
        if not local.is_file():
            res["status"] = f"refused: local payload missing {rel}"
            return res
        try:
            fs = scan_path(clamd, local, rel)
        except MalwareFound as e:
            res["status"] = f"MALWARE: {rel}: {e}"
            return res
        if fs.sha256 != manifest.get(rel):
            res["status"] = f"refused: scanned bytes of {rel} do not match manifest-sha256"
            return res
        scans.append(fs)
        if not fs.fully_scanned:
            not_full.append(rel)
        log(f"    {key}: {rel} {fs.method} ({fs.streams} stream(s)) ok")

    report = VirusScanReport(
        scanned_at=iso_now(), files_scanned=len(scans) - len(not_full), files_not_scanned=len(not_full),
        scanner_program=program, scanner_version=version, virus_definitions=defs,
        sip_root=str(bag), not_scanned_paths=not_full, file_scans=scans,
    )
    premis = build_rescan_premis(
        report, package_id=str(submission.get("package_id") or key),
        package_uuid=str(submission.get("package_uuid") or ""),
        repository_code=str(submission.get("repository_code") or "hitsave"),
        original_event=original_event,
    ).encode("utf-8")
    rel_new = rescan_relpath(on)
    tm_new = add_tagmanifest_line(tm_old, rel_new, sha256_hex(premis)).encode("utf-8")

    _put_verified(s3, bucket, f"{prefix}/{rel_new}", premis, "application/xml")
    _put_verified(s3, bucket, f"{prefix}/{TAGMANIFEST}", tm_new, "text/plain")
    check = parse_manifest(_get(s3, bucket, f"{prefix}/{TAGMANIFEST}").decode("utf-8"))
    if check.get(rel_new) != sha256_hex(_get(s3, bucket, f"{prefix}/{rel_new}")):
        raise RuntimeError(f"{key}: tagmanifest does not match uploaded rescan event")
    res["status"] = "rescanned" if not not_full else "rescanned (partial)"
    res["methods"] = {fs.rel: fs.method for fs in scans}

    lt = bag / TAGMANIFEST
    if lt.is_file() and lt.read_text(encoding="utf-8") == tm_old:
        (bag / rel_new).parent.mkdir(parents=True, exist_ok=True)
        (bag / rel_new).write_bytes(premis)
        lt.write_bytes(tm_new)
        res["local_bag"] = "updated"
    else:
        res["local_bag"] = "left as is (differs from Wasabi copy)"
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--game-key", default="")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    import psycopg
    import pyclamd

    from preservation_common import wasabi_bucket_for_storage_prefix
    from upload_bag_wasabi import load_wasabi_settings, make_s3_client, resolve_config_path
    from yaml_config import load_yaml

    ingest_path = Path("/config/preservation/ingest.yaml")
    ingest = load_yaml(ingest_path)
    wasabi, _ = load_wasabi_settings(ingest_path)
    creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), ingest_path.parent.parent))
    s3 = make_s3_client(wasabi, creds)
    clam = ingest["clamav"]
    clamd_factory = lambda: pyclamd.ClamdNetworkSocket(host=clam["host"], port=int(clam["port"]), timeout=30)  # noqa: E731
    clamd_factory().ping()
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    sql = ("SELECT game_key, ingest_kind, package_storage_prefix, aip_bag_path, inventory_json "
           "FROM game_ingest WHERE status = 'complete' AND inventory_json IS NOT NULL")
    params: list = []
    if args.game_key:
        sql += " AND game_key = %s"
        params.append(args.game_key)
    with psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                         user=db["user"], password=db["password"]) as conn:
        cur = conn.execute(sql + " ORDER BY game_key", params)
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    work = [r for r in rows if targets(r["inventory_json"])]
    if args.limit:
        work = work[: args.limit]
    total = sum(int(f["size_bytes"]) for r in work for f in r["inventory_json"]["files"]
                if int(f.get("size_bytes") or 0) > LEGACY_LIMIT)
    log(f"{len(work)} package(s), {total / 1e9:.1f} GB to scan "
        f"({'EXECUTE' if args.execute else 'dry-run'}, {args.workers} worker(s))")

    on = date.today().isoformat()
    results, counts = [], {}

    def one(row):
        prefix = str(row["package_storage_prefix"] or "").strip("/")
        bucket = wasabi_bucket_for_storage_prefix(wasabi, prefix, ingest_kind=row["ingest_kind"])
        try:
            return process(clamd_factory, s3, row=row, bucket=bucket, prefix=prefix,
                           execute=args.execute, on=on)
        except Exception as e:  # noqa: BLE001
            return {"game_key": row["game_key"], "status": f"error: {e}"}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(one, r) for r in work]
        for done, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            k = res["status"].split(":")[0]
            counts[k] = counts.get(k, 0) + 1
            log(f"[{done}/{len(work)}] {res['status']:<22} {res['game_key']}"
                + (f" local={res['local_bag']}" if "local_bag" in res else ""))
            results.append(res)
            if args.report:
                Path(args.report).write_text(json.dumps({"counts": counts, "results": results}, indent=2))
    log("Summary: " + json.dumps(counts))
    if any(k.startswith(("MALWARE", "error", "refused")) for k in counts):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
