#!/usr/bin/env python3
"""
Add catalogue scans (images from Omeka records: disc photos, sleeves, inserts) to a preserved bag's
PAYLOAD on Wasabi, as a new version of the bag.

Per package in the plan:
  - each source image is downloaded and stored as data/<package dir>/Scans/<name> (skipped when the
    same bytes are already anywhere in the payload, so re-runs and identical copies are no-ops);
  - the new files are virus-checked (clamav_full_scan, every byte); a detection aborts the package;
  - payload files are uploaded and verified by read-back BEFORE any tag file changes;
  - manifest-sha256.txt gains their lines, bag-info.txt gets the new Payload-Oxum,
    metadata/inventory.json the new entries + summary, and two PREMIS events are written:
    metadata/premis/catalogue-scans-<date>.xml (ingestion: what was added, from which Omeka media)
    and metadata/premis/catalogue-scans-virus-check-<date>.xml;
  - tagmanifest-sha256.txt (lines for every changed/new tag file) is uploaded LAST;
  - the bag on Wasabi is re-checked: payload object count/bytes == Payload-Oxum, every manifest and
    tagmanifest line for a changed file matches the object's bytes;
  - the ledger row's file_count / total_bytes / inventory_json follow the new inventory.
Existing payload files and their manifest lines are never rewritten. Previews (gallery, Scans list)
are rebuilt afterwards with refresh_bag_previews_wasabi.py.

Plan (JSON list): [{"game_key": K, "prefix": optional override (scratch rehearsal), "bucket": opt,
  "files": [{"url": U, "name": "<file name>", "omeka_media_id": N, "omeka_item_id": N}]}]
"name" should be the file's ORIGINAL uploaded name (Omeka media o:source): scans are preserved as
they are, never renamed for presentation.

Rename (--rename, plan entries carry "renames": [{"from": "data/...", "to": "data/..."}]): fixes the
names of files this tool added (same bytes): server-side copy to the new key, verified against the
manifest digest; manifest/inventory paths updated; PREMIS "filename change" event; tagmanifest last;
old objects deleted only after that; bag re-checked. Payload-Oxum is unchanged.

Usage (inside ingest-worker, clamd reachable; dry-run unless --execute):
  python scripts/add_catalogue_scans_wasabi.py --plan plan.json [--execute] [--no-ledger]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import mimetypes
import re
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

MANIFEST = "manifest-sha256.txt"
TAGMANIFEST = "tagmanifest-sha256.txt"
BAG_INFO = "bag-info.txt"
INVENTORY = "metadata/inventory.json"
SCANS_DIR = "Scans"


# ---------------------------------------------------------------- pure helpers (unit-tested)

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_manifest(text: str) -> dict[str, str]:
    """path -> sha256 (BagIt manifest; paths may contain spaces)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip():
            digest, path = line.split(None, 1)
            out[path.strip()] = digest.lower()
    return out


def payload_dir(inventory: dict) -> str:
    """The single top-level folder under data/ that the package's payload lives in."""
    tops = {f["path"].split("/")[1] for f in inventory["files"] if f["path"].startswith("data/")}
    if len(tops) != 1:
        raise ValueError(f"expected one payload folder under data/, found {sorted(tops)}")
    return tops.pop()


def safe_name(name: str) -> str:
    """File name for the payload: no path separators or control characters, no leading dots."""
    name = re.sub(r"[\x00-\x1f/\\]", "_", name).strip().lstrip(".")
    if not name:
        raise ValueError("empty file name")
    return name


def set_payload_oxum(bag_info: str, total_bytes: int, file_count: int) -> str:
    lines = bag_info.splitlines()
    new = f"Payload-Oxum: {total_bytes}.{file_count}"
    hits = [i for i, l in enumerate(lines) if l.lower().startswith("payload-oxum:")]
    if len(hits) != 1:
        raise ValueError(f"bag-info.txt has {len(hits)} Payload-Oxum lines")
    lines[hits[0]] = new
    return "\n".join(lines) + "\n"


def upsert_lines(manifest_text: str, digests: dict[str, str]) -> str:
    """Replace or append `<sha256>  <path>` lines for the given paths; keep every other line."""
    remaining = dict(digests)
    out = []
    for line in manifest_text.splitlines():
        if not line.strip():
            continue
        digest, path = line.split(None, 1)
        path = path.strip()
        out.append(f"{remaining.pop(path)}  {path}" if path in remaining else line)
    out.extend(f"{d}  {p}" for p, d in sorted(remaining.items()))
    return "\n".join(out) + "\n"


def plan_payload_additions(manifest: dict[str, str], top: str, new_files: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split new files into (add, skip). Each new file: {name, sha256, size, ...}.

    Skip when the same bytes are already in the payload (anywhere), or another new file has them.
    A different file at the same destination path is an error (never overwrite payload).
    """
    by_sha = {sha: path for path, sha in manifest.items()}
    add, skip = [], []
    for f in new_files:
        rel = f"data/{top}/{SCANS_DIR}/{safe_name(f['name'])}"
        if f["sha256"] in by_sha:
            skip.append(dict(f, rel=rel, reason=f"already in payload as {by_sha[f['sha256']]}"))
            continue
        if rel in manifest:
            raise ValueError(f"{rel} exists in the payload with different bytes; refusing to overwrite")
        by_sha[f["sha256"]] = rel
        add.append(dict(f, rel=rel))
    return add, skip


def updated_inventory(inventory: dict, added: list[dict]) -> dict:
    inv = copy.deepcopy(inventory)
    for f in added:
        inv["files"].append({
            "path": f["rel"],
            "size_bytes": int(f["size"]),
            "sha256": f["sha256"],
            "mime": f.get("mime") or mimetypes.guess_type(f["rel"])[0] or "application/octet-stream",
        })
    inv["files"].sort(key=lambda x: x["path"])
    exts = Counter(Path(x["path"]).suffix.lower().lstrip(".") or "(none)" for x in inv["files"])
    summary = dict(inv.get("summary") or {})
    summary.update({
        "file_count": len(inv["files"]),
        "total_bytes": sum(int(x["size_bytes"]) for x in inv["files"]),
        "by_extension": dict(sorted(exts.items())),
    })
    inv["summary"] = summary
    return inv


def ingestion_premis_xml(*, added: list[dict], package_id: str, package_uuid: str,
                         repository_code: str, when: str, premis_ns: str, xsi_ns: str) -> str:
    P = f"{{{premis_ns}}}"
    root = ET.Element(f"{P}event", {"version": "3.0",
                                     f"{{{xsi_ns}}}schemaLocation": f"{premis_ns} http://www.loc.gov/standards/premis/v3/premis.xsd"})
    ident = ET.SubElement(root, f"{P}eventIdentifier")
    ET.SubElement(ident, f"{P}eventIdentifierType").text = "UUID"
    ET.SubElement(ident, f"{P}eventIdentifierValue").text = str(uuid.uuid4())
    ET.SubElement(root, f"{P}eventType").text = "ingestion"
    ET.SubElement(root, f"{P}eventDateTime").text = when
    sources = "; ".join(
        f'{f["rel"]} <- Omeka media {f.get("omeka_media_id")} (item {f.get("omeka_item_id")}) sha256={f["sha256"]}'
        for f in added
    )
    detail = ET.SubElement(root, f"{P}eventDetailInformation")
    ET.SubElement(detail, f"{P}eventDetail").text = (
        f"Added {len(added)} catalogue scan(s) from archive.hitsave.org records to the payload as a new "
        f"bag version (existing payload unchanged): {sources}"
    )
    outcome = ET.SubElement(root, f"{P}eventOutcomeInformation")
    ET.SubElement(outcome, f"{P}eventOutcome").text = "success"
    agent = ET.SubElement(root, f"{P}linkingAgentIdentifier")
    ET.SubElement(agent, f"{P}linkingAgentIdentifierType").text = "local"
    ET.SubElement(agent, f"{P}linkingAgentIdentifierValue").text = f"{repository_code}:add_catalogue_scans_wasabi.py"
    obj = ET.SubElement(root, f"{P}linkingObjectIdentifier")
    ET.SubElement(obj, f"{P}linkingObjectIdentifierType").text = "UUID" if package_uuid else "local"
    ET.SubElement(obj, f"{P}linkingObjectIdentifierValue").text = package_uuid or package_id
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def rename_lines(manifest_text: str, renames: dict[str, str]) -> str:
    """Change the path of manifest lines (digest unchanged). Every 'from' must exist, no 'to' may."""
    present = parse_manifest(manifest_text)
    for old, new in renames.items():
        if old not in present:
            raise ValueError(f"{old} not in manifest")
        if new in present:
            raise ValueError(f"{new} already in manifest")
    out = []
    for line in manifest_text.splitlines():
        if not line.strip():
            continue
        digest, path = line.split(None, 1)
        path = path.strip()
        out.append(f"{digest}  {renames.get(path, path)}")
    return "\n".join(out) + "\n"


def renamed_inventory(inventory: dict, renames: dict[str, str]) -> dict:
    inv = copy.deepcopy(inventory)
    for f in inv["files"]:
        f["path"] = renames.get(f["path"], f["path"])
    inv["files"].sort(key=lambda x: x["path"])
    return inv


def filename_change_premis_xml(*, renames: dict[str, str], package_id: str, package_uuid: str,
                               repository_code: str, when: str, premis_ns: str, xsi_ns: str, reason: str) -> str:
    P = f"{{{premis_ns}}}"
    root = ET.Element(f"{P}event", {"version": "3.0",
                                     f"{{{xsi_ns}}}schemaLocation": f"{premis_ns} http://www.loc.gov/standards/premis/v3/premis.xsd"})
    ident = ET.SubElement(root, f"{P}eventIdentifier")
    ET.SubElement(ident, f"{P}eventIdentifierType").text = "UUID"
    ET.SubElement(ident, f"{P}eventIdentifierValue").text = str(uuid.uuid4())
    ET.SubElement(root, f"{P}eventType").text = "filename change"
    ET.SubElement(root, f"{P}eventDateTime").text = when
    detail = ET.SubElement(root, f"{P}eventDetailInformation")
    ET.SubElement(detail, f"{P}eventDetail").text = (
        f"{reason} Same bytes (manifest digests unchanged): " + "; ".join(f"{o} -> {n}" for o, n in renames.items()))
    outcome = ET.SubElement(root, f"{P}eventOutcomeInformation")
    ET.SubElement(outcome, f"{P}eventOutcome").text = "success"
    agent = ET.SubElement(root, f"{P}linkingAgentIdentifier")
    ET.SubElement(agent, f"{P}linkingAgentIdentifierType").text = "local"
    ET.SubElement(agent, f"{P}linkingAgentIdentifierValue").text = f"{repository_code}:add_catalogue_scans_wasabi.py"
    obj = ET.SubElement(root, f"{P}linkingObjectIdentifier")
    ET.SubElement(obj, f"{P}linkingObjectIdentifierType").text = "UUID" if package_uuid else "local"
    ET.SubElement(obj, f"{P}linkingObjectIdentifierValue").text = package_uuid or package_id
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# ---------------------------------------------------------------- I/O

def _get(s3, bucket: str, key: str) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def _put_verified(s3, bucket: str, key: str, data: bytes, content_type: str) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    if sha256_hex(_get(s3, bucket, key)) != sha256_hex(data):
        raise RuntimeError(f"read-back mismatch for s3://{bucket}/{key}")


def process(entry: dict, *, s3, clamd_factory, bucket: str, prefix: str, execute: bool, on: str) -> dict:
    import requests

    from clamav_full_scan import MalwareFound, scan_path
    from premis_virus_check import PREMIS_NS, XSI_NS, VirusScanReport, build_virus_check_premis_xml, clamav_version_info, iso_now
    from upload_bag_wasabi import upload_file_to_s3, verify_uploaded_object
    from s3_etag import hash_file

    root = prefix.strip("/") + "/"
    res: dict = {"game_key": entry["game_key"], "prefix": root}
    manifest_text = _get(s3, bucket, root + MANIFEST).decode("utf-8")
    tag_text = _get(s3, bucket, root + TAGMANIFEST).decode("utf-8")
    bag_info = _get(s3, bucket, root + BAG_INFO).decode("utf-8")
    inventory = json.loads(_get(s3, bucket, root + INVENTORY))
    submission = json.loads(_get(s3, bucket, root + "metadata/submission.json"))
    others = [o["Key"] for o in s3.list_objects_v2(Bucket=bucket, Prefix=root, Delimiter="/").get("Contents", [])
              if re.search(r"/(tag)?manifest-(?!sha256)\w+\.txt$", o["Key"])]
    if others:
        raise RuntimeError(f"unsupported extra manifests: {others}")
    manifest = parse_manifest(manifest_text)
    top = payload_dir(inventory)

    with tempfile.TemporaryDirectory(prefix="catscans-") as tmp:
        work = Path(tmp)
        fetched = []
        for i, f in enumerate(entry["files"]):
            local = work / f"{i:03d}-{safe_name(f['name'])}"
            r = requests.get(f["url"], timeout=300)
            r.raise_for_status()
            local.write_bytes(r.content)
            fetched.append(dict(f, local=str(local), sha256=sha256_hex(r.content), size=len(r.content)))
        add, skip = plan_payload_additions(manifest, top, fetched)
        res.update(added=[a["rel"] for a in add], skipped=[(s["rel"], s["reason"]) for s in skip])
        if not add:
            res["status"] = "nothing to add"
            return res

        clamd = clamd_factory()
        program, version, defs = clamav_version_info(clamd)
        scans = []
        for a in add:
            try:
                scans.append(scan_path(clamd, Path(a["local"]), a["rel"]))
            except MalwareFound as e:
                raise RuntimeError(f"MALWARE in {a['rel']}: {e}") from None
        not_scanned = [s.rel for s in scans if not s.fully_scanned]
        report = VirusScanReport(scanned_at=iso_now(), files_scanned=len(scans) - len(not_scanned),
                                 files_not_scanned=len(not_scanned), scanner_program=program,
                                 scanner_version=version, virus_definitions=defs,
                                 sip_root="catalogue-scans", not_scanned_paths=not_scanned, file_scans=scans)
        pkg_id = str(submission.get("package_id") or entry["game_key"])
        pkg_uuid = str(submission.get("package_uuid") or "")
        repo = str(submission.get("repository_code") or "hitsave")
        virus_xml = build_virus_check_premis_xml(report, package_id=pkg_id, package_uuid=pkg_uuid,
                                                 repository_code=repo, linking_agent="add_catalogue_scans_wasabi.py")
        ingest_xml = ingestion_premis_xml(added=add, package_id=pkg_id, package_uuid=pkg_uuid,
                                          repository_code=repo, when=iso_now(), premis_ns=PREMIS_NS, xsi_ns=XSI_NS)

        new_inventory = updated_inventory(inventory, add)
        new_manifest = upsert_lines(manifest_text, {a["rel"]: a["sha256"] for a in add})
        new_bag_info = set_payload_oxum(bag_info, new_inventory["summary"]["total_bytes"],
                                        new_inventory["summary"]["file_count"])
        premis_ingest = f"metadata/premis/catalogue-scans-{on}.xml"
        premis_virus = f"metadata/premis/catalogue-scans-virus-check-{on}.xml"
        if any(p in parse_manifest(tag_text) for p in (premis_ingest, premis_virus)):
            raise RuntimeError(f"PREMIS event for {on} already exists; run again another day or merge by hand")
        tag_files = {
            MANIFEST: new_manifest.encode("utf-8"),
            BAG_INFO: new_bag_info.encode("utf-8"),
            INVENTORY: (json.dumps(new_inventory, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
            premis_ingest: ingest_xml.encode("utf-8"),
            premis_virus: virus_xml.encode("utf-8"),
        }
        new_tag = upsert_lines(tag_text, {p: sha256_hex(b) for p, b in tag_files.items()})
        res.update(new_oxum=f'{new_inventory["summary"]["total_bytes"]}.{new_inventory["summary"]["file_count"]}',
                   virus=f"{report.files_scanned} scanned, {report.files_not_scanned} not fully")
        if not execute:
            res["status"] = "dry-run"
            return res

        # 1. payload first (verified), 2. tag files, 3. tagmanifest last
        for a in add:
            hashes = hash_file(Path(a["local"]))
            upload_file_to_s3(s3, bucket=bucket, key=root + a["rel"], path=Path(a["local"]))
            verify_uploaded_object(s3, bucket=bucket, key=root + a["rel"], size=hashes.size,
                                   local_digest=hashes.sha256, local_hashes=hashes)
        types = {".txt": "text/plain", ".json": "application/json", ".xml": "application/xml"}
        for p, b in tag_files.items():
            _put_verified(s3, bucket, root + p, b, types.get(Path(p).suffix, "application/octet-stream"))
        _put_verified(s3, bucket, root + TAGMANIFEST, new_tag.encode("utf-8"), "text/plain")

    # 4. verify the bag on Wasabi
    objs = [o for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=root + "data/")
            for o in page.get("Contents", [])]
    oxum_bytes, oxum_count = (int(x) for x in res["new_oxum"].split("."))
    if (sum(o["Size"] for o in objs), len(objs)) != (oxum_bytes, oxum_count):
        raise RuntimeError(f"Payload-Oxum mismatch: listing {sum(o['Size'] for o in objs)}.{len(objs)} vs {res['new_oxum']}")
    live_tag = parse_manifest(_get(s3, bucket, root + TAGMANIFEST).decode("utf-8"))
    for p in tag_files:
        if live_tag.get(p) != sha256_hex(_get(s3, bucket, root + p)):
            raise RuntimeError(f"tagmanifest line for {p} does not match the object")
    res.update(status="updated", inventory=new_inventory)
    return res


def process_rename(entry: dict, *, s3, bucket: str, prefix: str, execute: bool, on: str) -> dict:
    from premis_virus_check import PREMIS_NS, XSI_NS, iso_now

    root = prefix.strip("/") + "/"
    renames = {r["from"]: r["to"] for r in entry["renames"]}
    for new in renames.values():
        if not new.startswith("data/") or "/../" in new:
            raise ValueError(f"bad target {new}")
    res: dict = {"game_key": entry["game_key"], "prefix": root, "renames": renames}
    manifest_text = _get(s3, bucket, root + MANIFEST).decode("utf-8")
    tag_text = _get(s3, bucket, root + TAGMANIFEST).decode("utf-8")
    inventory = json.loads(_get(s3, bucket, root + INVENTORY))
    submission = json.loads(_get(s3, bucket, root + "metadata/submission.json"))
    manifest = parse_manifest(manifest_text)
    new_manifest = rename_lines(manifest_text, renames)
    new_inventory = renamed_inventory(inventory, renames)
    premis = f"metadata/premis/catalogue-scans-filename-change-{on}.xml"
    if premis in parse_manifest(tag_text):
        raise RuntimeError(f"{premis} already exists")
    xml = filename_change_premis_xml(
        renames=renames, package_id=str(submission.get("package_id") or entry["game_key"]),
        package_uuid=str(submission.get("package_uuid") or ""), repository_code=str(submission.get("repository_code") or "hitsave"),
        when=iso_now(), premis_ns=PREMIS_NS, xsi_ns=XSI_NS,
        reason=entry.get("reason") or "Catalogue scans renamed to their original uploaded file names.")
    tag_files = {MANIFEST: new_manifest.encode("utf-8"),
                 INVENTORY: (json.dumps(new_inventory, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
                 premis: xml.encode("utf-8")}
    new_tag = upsert_lines(tag_text, {p: sha256_hex(b) for p, b in tag_files.items()})
    if not execute:
        res["status"] = "dry-run"
        return res
    for old, new in renames.items():          # 1. copy, verify against the manifest digest
        s3.copy({"Bucket": bucket, "Key": root + old}, bucket, root + new)
        if sha256_hex(_get(s3, bucket, root + new)) != manifest[old]:
            raise RuntimeError(f"copy of {old} does not match its manifest digest")
    types = {".txt": "text/plain", ".json": "application/json", ".xml": "application/xml"}
    for p, b in tag_files.items():             # 2. tag files, 3. tagmanifest last
        _put_verified(s3, bucket, root + p, b, types.get(Path(p).suffix, "application/octet-stream"))
    _put_verified(s3, bucket, root + TAGMANIFEST, new_tag.encode("utf-8"), "text/plain")
    for old in renames:                        # 4. only now remove the old names
        s3.delete_object(Bucket=bucket, Key=root + old)
    objs = [o for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=root + "data/")
            for o in page.get("Contents", [])]
    oxum = re.search(r"Payload-Oxum:\s*(\d+)\.(\d+)", _get(s3, bucket, root + BAG_INFO).decode("utf-8"))
    if (sum(o["Size"] for o in objs), len(objs)) != (int(oxum.group(1)), int(oxum.group(2))):
        raise RuntimeError("Payload-Oxum mismatch after rename")
    live_tag = parse_manifest(_get(s3, bucket, root + TAGMANIFEST).decode("utf-8"))
    for p in tag_files:
        if live_tag.get(p) != sha256_hex(_get(s3, bucket, root + p)):
            raise RuntimeError(f"tagmanifest line for {p} does not match")
    res.update(status="renamed", inventory=new_inventory)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--no-ledger", action="store_true", help="don't touch the ledger (scratch rehearsals)")
    ap.add_argument("--rename", action="store_true", help="plan entries carry 'renames' instead of 'files'")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    import psycopg
    import pyclamd

    from preservation_common import resolve_wasabi_bucket
    from upload_bag_wasabi import load_wasabi_settings, make_s3_client, resolve_config_path
    from yaml_config import load_yaml

    ingest_path = Path("/config/preservation/ingest.yaml")
    ingest = load_yaml(ingest_path)
    wasabi, _ = load_wasabi_settings(ingest_path)
    creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), ingest_path.parent.parent))
    s3 = make_s3_client(wasabi, creds)
    clam = ingest["clamav"]
    clamd_factory = lambda: pyclamd.ClamdNetworkSocket(host=clam["host"], port=int(clam["port"]), timeout=60)  # noqa: E731
    clamd_factory().ping()
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    connect = lambda: psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],  # noqa: E731
                                      user=db["user"], password=db["password"])
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    on = date.today().isoformat()
    results = []
    for entry in plan:
        bucket = entry.get("bucket") or resolve_wasabi_bucket(wasabi, "dump")
        prefix = entry.get("prefix")
        if not prefix:
            with connect() as conn:
                row = conn.execute("SELECT package_storage_prefix FROM game_ingest WHERE game_key = %s AND status = 'complete'",
                                   (entry["game_key"],)).fetchone()
            if not row:
                results.append({"game_key": entry["game_key"], "status": "no complete ledger row"})
                continue
            prefix = row[0]
        try:
            if args.rename:
                res = process_rename(entry, s3=s3, bucket=bucket, prefix=prefix, execute=args.execute, on=on)
            else:
                res = process(entry, s3=s3, clamd_factory=clamd_factory, bucket=bucket, prefix=prefix,
                              execute=args.execute, on=on)
        except Exception as exc:  # noqa: BLE001 - report per package, keep going
            res = {"game_key": entry["game_key"], "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
        inv = res.pop("inventory", None)
        if inv is not None and args.execute and not args.no_ledger:
            with connect() as conn:
                conn.execute("UPDATE game_ingest SET file_count = %s, total_bytes = %s, inventory_json = %s, updated_at = NOW() "
                             "WHERE game_key = %s AND status = 'complete'",
                             (inv["summary"]["file_count"], inv["summary"]["total_bytes"], json.dumps(inv), entry["game_key"]))
                conn.commit()
            res["ledger"] = "updated"
        results.append(res)
        print(json.dumps(res, ensure_ascii=False), flush=True)
    if args.report:
        Path(args.report).write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if any(r.get("status") == "FAILED" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
