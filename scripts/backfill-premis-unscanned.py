#!/usr/bin/env python3
"""
Correct PREMIS virus-check records that claim a full ClamAV scan for bags holding files over
the ClamAV size limit (those files were never scanned; see premis_virus_check.scan_sip_tree).

Per affected package (ledger status=complete, a payload file larger than the limit, PREMIS
outcome "success"):
  - the existing event keeps its identifier, date and agents;
  - eventDetail gets filesScanned reduced by the oversized count, filesNotScanned, scanLimitBytes;
  - eventOutcome becomes "partial"; the note lists the unscanned files and records the correction;
  - only metadata/premis/virus-check-event.xml and its tagmanifest-sha256.txt line change.
    The payload, manifest-sha256.txt and bag-info.txt are never touched.

Safety: refuses a package whose current PREMIS digest doesn't match its tagmanifest line, skips
records that are not "success" (already partial / corrected), verifies every upload by reading it
back, and only updates the local bag copy when it is identical to what Wasabi had.

Dry-run by default. Usage (inside ingest-worker):
  python scripts/backfill-premis-unscanned.py [--execute] [--limit N] [--game-key K] [--kind dump]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from premis_virus_check import PREMIS_NS, XSI_NS, clamd_size_limit  # noqa: E402

PREMIS_REL = "metadata/premis/virus-check-event.xml"
TAGMANIFEST = "tagmanifest-sha256.txt"
CORRECTION_MARK = "Record corrected"
P = f"{{{PREMIS_NS}}}"

ET.register_namespace("premis", PREMIS_NS)
ET.register_namespace("xsi", XSI_NS)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def oversized_files(inventory: dict | None, limit: int) -> list[str]:
    """Payload paths (as in the bag, e.g. data/…) larger than the ClamAV limit."""
    out = []
    for f in (inventory or {}).get("files") or []:
        if int(f.get("size_bytes") or 0) > limit:
            out.append(str(f.get("path")))
    return sorted(out)


def tagmanifest_digest(tagmanifest: str, relpath: str) -> str | None:
    for line in tagmanifest.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1] == relpath:
            return parts[0].lower()
    return None


def replace_tagmanifest_digest(tagmanifest: str, relpath: str, digest: str) -> str:
    """Change only relpath's digest; every other line is kept byte-for-byte."""
    out, found = [], False
    for line in tagmanifest.splitlines(keepends=True):
        parts = line.rstrip("\r\n").split(None, 1)
        if len(parts) == 2 and parts[1] == relpath:
            sep = re.match(r"[ \t]+", line[len(parts[0]) :]).group(0)
            ending = line[len(line.rstrip("\r\n")) :]
            out.append(f"{digest}{sep}{parts[1]}{ending}")
            found = True
        else:
            out.append(line)
    if not found:
        raise ValueError(f"{relpath} not in {TAGMANIFEST}")
    return "".join(out)


def premis_outcome(xml_text: str) -> str:
    root = ET.fromstring(xml_text.encode("utf-8"))
    el = root.find(f"{P}eventOutcomeInformation/{P}eventOutcome")
    return (el.text or "").strip() if el is not None else ""


def correct_premis(xml_text: str, *, unscanned: list[str], limit: int, corrected_on: str) -> str:
    """Return corrected PREMIS XML (see module docstring). Raises if the record isn't 'success'."""
    root = ET.fromstring(xml_text.encode("utf-8"))
    outcome_el = root.find(f"{P}eventOutcomeInformation/{P}eventOutcome")
    note_el = root.find(f"{P}eventOutcomeInformation/{P}eventOutcomeDetailNote")
    detail_el = root.find(f"{P}eventDetailInformation/{P}eventDetail")
    if outcome_el is None or detail_el is None:
        raise ValueError("PREMIS event lacks eventOutcome or eventDetail")
    if (outcome_el.text or "").strip() != "success":
        raise ValueError(f"PREMIS outcome is {outcome_el.text!r}, not 'success'; nothing to correct")
    if note_el is not None and CORRECTION_MARK in (note_el.text or ""):
        raise ValueError("PREMIS record was already corrected")

    k = len(unscanned)
    detail = detail_el.text or ""
    m = re.search(r"filesScanned=(\d+)", detail)
    if m:
        scanned = max(int(m.group(1)) - k, 0)
        detail = detail.replace(
            m.group(0), f"filesScanned={scanned}; filesNotScanned={k}; scanLimitBytes={limit}", 1
        )
    else:
        scanned = None
        detail = f"{detail}; filesNotScanned={k}; scanLimitBytes={limit}".lstrip("; ")
    detail_el.text = detail

    outcome_el.text = "partial"
    shown = ", ".join(unscanned[:20]) + (f" … and {k - 20} more" if k > 20 else "")
    scanned_txt = f"in {scanned} scanned file(s)" if scanned is not None else "in the scanned files"
    original = (note_el.text or "").strip() if note_el is not None else ""
    note = (
        f"No malware detected {scanned_txt}. {k} file(s) larger than the ClamAV scan limit "
        f"({limit} bytes) were NOT scanned: {shown}. "
        f"{CORRECTION_MARK} on {corrected_on} by backfill-premis-unscanned.py; "
        f"the original record stated: \"{original}\""
    )
    if note_el is None:
        note_el = ET.SubElement(root.find(f"{P}eventOutcomeInformation"), f"{P}eventOutcomeDetailNote")
    note_el.text = note

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# --- I/O ---------------------------------------------------------------------------------------


def _get(s3, bucket: str, key: str) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def _put_verified(s3, bucket: str, key: str, data: bytes, content_type: str) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    back = _get(s3, bucket, key)
    if sha256_hex(back) != sha256_hex(data):
        raise RuntimeError(f"read-back mismatch after upload of s3://{bucket}/{key}")


def process_package(s3, *, bucket: str, prefix: str, row: dict, limit: int, execute: bool, corrected_on: str) -> dict:
    game_key = row["game_key"]
    unscanned = oversized_files(row["inventory_json"], limit)
    result = {"game_key": game_key, "bucket": bucket, "prefix": prefix, "unscanned": unscanned}
    premis_key, tm_key = f"{prefix}/{PREMIS_REL}", f"{prefix}/{TAGMANIFEST}"

    premis_old = _get(s3, bucket, premis_key)
    tm_old = _get(s3, bucket, tm_key).decode("utf-8")
    listed = tagmanifest_digest(tm_old, PREMIS_REL)
    if listed != sha256_hex(premis_old):
        result["status"] = "refused: PREMIS digest does not match tagmanifest (bag inconsistent)"
        return result
    outcome = premis_outcome(premis_old.decode("utf-8"))
    if outcome != "success":
        result["status"] = f"skipped: outcome already {outcome!r}"
        return result

    premis_new = correct_premis(
        premis_old.decode("utf-8"), unscanned=unscanned, limit=limit, corrected_on=corrected_on
    ).encode("utf-8")
    tm_new = replace_tagmanifest_digest(tm_old, PREMIS_REL, sha256_hex(premis_new)).encode("utf-8")
    result["premis_sha256"] = {"old": listed, "new": sha256_hex(premis_new)}
    if not execute:
        result["status"] = "would correct"
        return result

    _put_verified(s3, bucket, premis_key, premis_new, "application/xml")
    _put_verified(s3, bucket, tm_key, tm_new, "text/plain")
    check = _get(s3, bucket, tm_key).decode("utf-8")
    if tagmanifest_digest(check, PREMIS_REL) != sha256_hex(_get(s3, bucket, premis_key)):
        raise RuntimeError(f"{game_key}: tagmanifest does not match uploaded PREMIS")
    result["status"] = "corrected"

    local = Path(str(row.get("aip_bag_path") or ""))
    lp, lt = local / PREMIS_REL, local / TAGMANIFEST
    if row.get("aip_bag_path") and lp.is_file() and lt.is_file():
        if sha256_hex(lp.read_bytes()) == listed and lt.read_text(encoding="utf-8") == tm_old:
            lp.write_bytes(premis_new)
            lt.write_bytes(tm_new)
            result["local_bag"] = "updated"
        else:
            result["local_bag"] = "left as is (differs from Wasabi copy)"
    else:
        result["local_bag"] = "not present"
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--execute", action="store_true", help="write corrections (default: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="max packages to process (0 = all)")
    ap.add_argument("--game-key", default="")
    ap.add_argument("--kind", default="", help="ingest_kind filter (dump, press_material, …)")
    ap.add_argument("--report", default="", help="write JSON report to this path")
    args = ap.parse_args()

    import psycopg

    from preservation_common import wasabi_bucket_for_storage_prefix
    from upload_bag_wasabi import load_wasabi_settings, make_s3_client, resolve_config_path
    from yaml_config import load_yaml

    limit = clamd_size_limit()
    if not limit:
        raise SystemExit("Could not read the ClamAV size limit from clamd.conf")
    ingest_path = Path("/config/preservation/ingest.yaml")
    ingest = load_yaml(ingest_path)
    wasabi, _ = load_wasabi_settings(ingest_path)
    creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), ingest_path.parent.parent))
    s3 = make_s3_client(wasabi, creds)
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]

    sql = (
        "SELECT game_key, ingest_kind, package_storage_prefix, aip_bag_path, inventory_json "
        "FROM game_ingest WHERE status = 'complete' AND inventory_json IS NOT NULL"
    )
    params: list = []
    if args.game_key:
        sql += " AND game_key = %s"
        params.append(args.game_key)
    if args.kind:
        sql += " AND ingest_kind = %s"
        params.append(args.kind)
    sql += " ORDER BY game_key"
    with psycopg.connect(
        host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
        user=db["user"], password=db["password"],
    ) as conn:
        cur = conn.execute(sql, params)
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    affected = [r for r in rows if oversized_files(r["inventory_json"], limit)]
    if args.limit:
        affected = affected[: args.limit]
    print(f"ClamAV limit {limit} bytes; {len(affected)} package(s) with oversized files "
          f"({'EXECUTE' if args.execute else 'dry-run'})", flush=True)

    corrected_on = date.today().isoformat()
    results, counts = [], {}
    for row in affected:
        prefix = str(row["package_storage_prefix"] or "").strip("/")
        bucket = wasabi_bucket_for_storage_prefix(wasabi, prefix, ingest_kind=row["ingest_kind"])
        try:
            res = process_package(
                s3, bucket=bucket, prefix=prefix, row=row, limit=limit,
                execute=args.execute, corrected_on=corrected_on,
            )
        except Exception as e:  # noqa: BLE001 - report and continue with the next package
            res = {"game_key": row["game_key"], "status": f"error: {e}"}
        key = res["status"].split(":")[0]
        counts[key] = counts.get(key, 0) + 1
        print(f"  {res['status']:<16} {row['game_key']} ({len(res.get('unscanned') or [])} unscanned)"
              + (f" local={res['local_bag']}" if "local_bag" in res else ""), flush=True)
        results.append(res)

    print("Summary:", json.dumps(counts))
    if args.report:
        Path(args.report).write_text(json.dumps({"counts": counts, "results": results}, indent=2))
    if any(k.startswith(("error", "refused")) for k in counts):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
