#!/usr/bin/env python3
"""Align Wasabi dump BagIt tag files with Postgres ledger (after slug/prefix rename)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import psycopg
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from package_identity import parse_package_id  # noqa: E402
from preservation_common import wasabi_bucket_for_storage_prefix  # noqa: E402
from upload_bag_wasabi import (  # noqa: E402
    load_wasabi_settings,
    make_s3_client,
    resolve_config_path,
    upload_file_to_s3,
)
from yaml_config import dumps_json, load_yaml  # noqa: E402

PREMIS_NS = "http://www.loc.gov/premis/v3"


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def s3_get_bytes(client, bucket: str, key: str) -> bytes:
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def update_manifest_digest(manifest_text: str, relpath: str, digest: str) -> str:
    lines: list[str] = []
    found = False
    for line in manifest_text.splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ValueError(f"Unexpected manifest line: {line!r}")
        _old_digest, path = parts
        sep = "  " if "  " in line else " "
        if path == relpath:
            lines.append(f"{digest}{sep}{path}")
            found = True
        else:
            lines.append(line)
    if not found:
        raise ValueError(f"Path {relpath!r} not in manifest")
    return "\n".join(lines) + "\n"


def patch_bag_info_external_description(
    body: str,
    *,
    omeka_title: str,
    package_id: str,
    wasabi_uri: str,
    submission_date: str,
) -> str:
    new_line = (
        f"External-Description: {omeka_title}; package_id={package_id}; "
        f"wasabi={wasabi_uri}; submission_date={submission_date}"
    )
    if re.search(r"^External-Description:", body, flags=re.MULTILINE):
        return re.sub(r"^External-Description:.*", new_line, body, count=1, flags=re.MULTILINE)
    return body.rstrip() + "\n" + new_line + "\n"


def patch_submission_json(submission: dict, *, ledger: dict, storage_prefix: str, wasabi_uri: str) -> dict:
    game_key = str(ledger["game_key"])
    title_slug = str(ledger.get("title_slug") or parse_package_id(game_key)[0])
    out = dict(submission)
    out["package_id"] = game_key
    out["game_key"] = game_key
    out["title_slug"] = title_slug
    out["sort_letter"] = str(ledger.get("sort_letter") or out.get("sort_letter") or "")
    out["wasabi_key"] = storage_prefix
    out["package_storage_prefix"] = storage_prefix
    if wasabi_uri:
        out["aip_wasabi_prefix"] = wasabi_uri.rstrip("/") + "/"
    return out


def patch_premis_package_id(xml_text: str, package_id: str) -> bytes:
    root = ET.fromstring(xml_text)
    for elem in root.iter(f"{{{PREMIS_NS}}}linkingObjectIdentifier"):
        id_type = elem.find(f"{{{PREMIS_NS}}}linkingObjectIdentifierType")
        id_val = elem.find(f"{{{PREMIS_NS}}}linkingObjectIdentifierValue")
        if id_type is not None and id_val is not None and (id_type.text or "").strip() == "local package identifier":
            id_val.text = package_id
    ET.indent(root, space="  ")
    xml_body = ET.tostring(root, encoding="unicode", xml_declaration=False)
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" + xml_body + "\n").encode("utf-8")


def sync_bag_from_ledger(
    client,
    *,
    bucket: str,
    storage_prefix: str,
    ledger_row: dict,
    execute: bool,
) -> dict:
    prefix = str(storage_prefix).strip("/")
    game_key = str(ledger_row["game_key"])
    aip = str(ledger_row.get("aip_wasabi_prefix") or f"s3://{bucket}/{prefix}/").rstrip("/") + "/"

    sub_key = f"{prefix}/metadata/submission.json"
    bag_info_key = f"{prefix}/bag-info.txt"
    premis_key = f"{prefix}/metadata/premis/virus-check-event.xml"
    tagmanifest_key = f"{prefix}/tagmanifest-sha256.txt"

    submission = json.loads(s3_get_bytes(client, bucket, sub_key).decode("utf-8"))
    omeka_title = str(submission.get("omeka_item_title") or "").strip()
    submission_date = str(submission.get("submission_date") or "")

    new_sub = patch_submission_json(
        submission,
        ledger=ledger_row,
        storage_prefix=prefix,
        wasabi_uri=aip,
    )
    sub_bytes = dumps_json(new_sub, indent=2).encode("utf-8") + b"\n"

    bag_info_body = s3_get_bytes(client, bucket, bag_info_key).decode("utf-8")
    bag_info_body = patch_bag_info_external_description(
        bag_info_body,
        omeka_title=omeka_title,
        package_id=game_key,
        wasabi_uri=aip,
        submission_date=submission_date,
    )
    bag_info_bytes = bag_info_body.encode("utf-8")
    if not bag_info_body.endswith("\n"):
        bag_info_bytes += b"\n"

    premis_bytes: bytes | None = None
    try:
        premis_raw = s3_get_bytes(client, bucket, premis_key).decode("utf-8")
        premis_bytes = patch_premis_package_id(premis_raw, game_key)
    except Exception as exc:
        from botocore.exceptions import ClientError

        if not isinstance(exc, ClientError) or exc.response.get("Error", {}).get("Code") != "NoSuchKey":
            raise

    tagmanifest = s3_get_bytes(client, bucket, tagmanifest_key).decode("utf-8")
    tagmanifest = update_manifest_digest(tagmanifest, "bag-info.txt", sha256_hex(bag_info_bytes))
    tagmanifest = update_manifest_digest(tagmanifest, "metadata/submission.json", sha256_hex(sub_bytes))
    if premis_bytes is not None:
        tagmanifest = update_manifest_digest(
            tagmanifest, "metadata/premis/virus-check-event.xml", sha256_hex(premis_bytes)
        )
    tagmanifest_bytes = tagmanifest.encode("utf-8")

    plan = {
        "game_key": game_key,
        "bucket": bucket,
        "prefix": prefix,
        "wasabi_uri": aip,
        "files": ["metadata/submission.json", "bag-info.txt", "tagmanifest-sha256.txt"],
    }
    if premis_bytes is not None:
        plan["files"].insert(1, "metadata/premis/virus-check-event.xml")

    if not execute:
        plan["dry_run"] = True
        return plan

    uploads = [
        (sub_key, sub_bytes),
        (bag_info_key, bag_info_bytes),
        (tagmanifest_key, tagmanifest_bytes),
    ]
    if premis_bytes is not None:
        uploads.insert(1, (premis_key, premis_bytes))

    for key, payload in uploads:
        tmp = Path("/tmp") / f"bag-sync-{hashlib.sha256(key.encode()).hexdigest()[:12]}"
        tmp.write_bytes(payload)
        try:
            upload_file_to_s3(client, bucket=bucket, key=key, path=tmp)
            print(f"  uploaded s3://{bucket}/{key} ({len(payload)} bytes)")
        finally:
            tmp.unlink(missing_ok=True)

    plan["dry_run"] = False
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-key", required=True, help="Ledger game_key for complete dump")
    parser.add_argument("--execute", action="store_true", help="Upload patched tag files to Wasabi")
    args = parser.parse_args()

    ingest_path = Path("/config/preservation/ingest.yaml")
    ingest = load_yaml(ingest_path)
    db_cfg = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    wasabi_cfg, _ = load_wasabi_settings(ingest_path)
    creds = load_yaml(resolve_config_path(str(wasabi_cfg["credentials_file"]), ingest_path.parent.parent))
    client = make_s3_client(wasabi_cfg, creds)

    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, title_slug, package_storage_prefix, aip_wasabi_prefix, sort_letter
                FROM game_ingest
                WHERE game_key = %s AND ingest_kind = 'dump' AND status = 'complete'
                """,
                (args.game_key,),
            )
            row = cur.fetchone()
    if not row:
        raise SystemExit(f"No complete dump ledger row for game_key={args.game_key!r}")

    ledger_row = {
        "game_key": row[0],
        "title_slug": row[1],
        "package_storage_prefix": row[2],
        "aip_wasabi_prefix": row[3],
        "sort_letter": row[4],
    }
    prefix = str(ledger_row["package_storage_prefix"] or "").strip("/")
    if not prefix:
        raise SystemExit("Ledger missing package_storage_prefix")
    bucket = wasabi_bucket_for_storage_prefix(wasabi_cfg, prefix, ingest_kind="dump")

    plan = sync_bag_from_ledger(
        client,
        bucket=bucket,
        storage_prefix=prefix,
        ledger_row=ledger_row,
        execute=args.execute,
    )
    print(json.dumps(plan, indent=2))
    if not args.execute:
        print("Dry run only; pass --execute to upload.", file=sys.stderr)


if __name__ == "__main__":
    main()
