#!/usr/bin/env python3
"""Correct press-material Omeka catalog fields and BagIt metadata on Wasabi (one package)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

try:
    import psycopg
    import requests
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from omeka_catalog_common import load_register_dip_omeka_api  # noqa: E402
from sync_dump_bag_metadata_from_ledger import (  # noqa: E402
    patch_bag_info_external_description,
    s3_get_bytes,
    sha256_hex,
    update_manifest_digest,
)
from preservation_common import wasabi_bucket_for_storage_prefix  # noqa: E402
from upload_bag_wasabi import (  # noqa: E402
    load_wasabi_settings,
    make_s3_client,
    resolve_config_path,
    upload_file_to_s3,
)
from yaml_config import dumps_json, load_yaml  # noqa: E402

register_api = load_register_dip_omeka_api()


def _ledger_columns() -> list[str]:
    return [
        "game_key",
        "omeka_item_id",
        "package_storage_prefix",
        "aip_wasabi_prefix",
        "title_slug",
        "sort_letter",
        "inventory_json",
    ]


def _row_to_ledger(row: tuple) -> dict:
    out = dict(zip(_ledger_columns(), row))
    if out["omeka_item_id"] is None:
        raise SystemExit(f"Ledger row has no omeka_item_id: {out['game_key']!r}")
    return out


def _ledger_connect():
    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    return register_api.pg_connect(db_cfg)


def _ledger_row(game_key: str) -> dict:
    conn = _ledger_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, omeka_item_id, package_storage_prefix, aip_wasabi_prefix,
                       title_slug, sort_letter, inventory_json
                FROM game_ingest
                WHERE game_key = %s
                """,
                (game_key,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise SystemExit(f"No ledger row for game_key={game_key!r}")
    return _row_to_ledger(row)


def _ledger_row_by_omeka_item_id(item_id: int) -> dict:
    conn = _ledger_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, omeka_item_id, package_storage_prefix, aip_wasabi_prefix,
                       title_slug, sort_letter, inventory_json
                FROM game_ingest
                WHERE omeka_item_id = %s
                """,
                (item_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise SystemExit(f"No ledger row for omeka_item_id={item_id}")
    return _row_to_ledger(row)


def patch_omeka(
    *,
    item_id: int,
    title: str,
    description: str,
    reference_uri: str,
    former_title: str,
) -> None:
    api_cfg = load_yaml(CONFIG_ROOT / "omeka-catalog.yaml")["api"]
    cred_rel = Path(api_cfg["credentials_file"])
    if cred_rel.parts and cred_rel.parts[0] == "config":
        cred_rel = Path(*cred_rel.parts[1:])
    creds = load_yaml(CONFIG_ROOT / cred_rel)
    session = requests.Session()
    base = api_cfg["base_url"]

    get_url = register_api.api_url(base, f"items/{item_id}", creds)
    resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"GET item {item_id} failed ({resp.status_code}): {resp.text[:800]}")
    item_body = resp.json()

    title_pid = register_api.resolve_property_id(session, base, creds, "dcterms:title")
    desc_pid = register_api.resolve_property_id(session, base, creds, "dcterms:description")
    bibo_pid = register_api.resolve_property_id(session, base, creds, "bibo:uri")

    bibo_rows: list[dict] = []
    for row in item_body.get("bibo:uri") or []:
        uri = (row.get("@id") or "").strip()
        if uri.startswith("s3://"):
            continue
        if uri == reference_uri:
            continue
        bibo_rows.append(row)
    bibo_rows.append(register_api.uri_value(bibo_pid, reference_uri, is_public=True))

    updates = {
        "dcterms:title": [register_api.literal_value(title_pid, title)],
        "dcterms:description": [register_api.literal_value(desc_pid, description)],
        "bibo:uri": bibo_rows,
    }
    register_api.patch_item_rdf_values(
        session, base, creds, item_id, updates, item_body=item_body
    )

    patch_url = register_api.api_url(base, f"items/{item_id}", creds)
    title_patch = session.patch(
        patch_url,
        headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
        json={"o:title": title},
        timeout=120,
    )
    if not title_patch.ok:
        raise RuntimeError(f"o:title PATCH failed ({title_patch.status_code}): {title_patch.text[:800]}")

    print(
        json.dumps(
            {
                "omeka_item_id": item_id,
                "title": title,
                "reference_uri": reference_uri,
                "former_title": former_title,
            },
            indent=2,
        )
    )


def patch_wasabi_bag(
    *,
    ledger: dict,
    title: str,
    reference_uri: str,
    former_title: str,
    correction_summary: str,
    execute: bool,
) -> dict:
    prefix = str(ledger["package_storage_prefix"]).strip("/")
    ingest_path = CONFIG_ROOT / "preservation/ingest.yaml"
    wasabi_cfg, cred_path = load_wasabi_settings(ingest_path)
    creds = load_yaml(resolve_config_path(str(wasabi_cfg["credentials_file"]), ingest_path.parent.parent))
    client = make_s3_client(wasabi_cfg, creds)
    bucket = wasabi_bucket_for_storage_prefix(wasabi_cfg, prefix, ingest_kind="press_material")

    sub_key = f"{prefix}/metadata/submission.json"
    bag_info_key = f"{prefix}/bag-info.txt"
    tagmanifest_key = f"{prefix}/tagmanifest-sha256.txt"

    submission = json.loads(s3_get_bytes(client, bucket, sub_key).decode("utf-8"))
    submission["omeka_item_title"] = title
    notes = list(submission.get("catalog_notes") or [])
    notes.append(
        {
            "date": date.today().isoformat(),
            "former_omeka_item_title": former_title,
            "summary": correction_summary,
            "reference_url": reference_uri,
        }
    )
    submission["catalog_notes"] = notes
    sub_bytes = dumps_json(submission, indent=2).encode("utf-8") + b"\n"

    game_key = str(ledger["game_key"])
    aip = str(ledger.get("aip_wasabi_prefix") or f"s3://{bucket}/{prefix}/").rstrip("/") + "/"
    submission_date = str(submission.get("submission_date") or submission.get("ingested_at") or "")

    bag_info_body = s3_get_bytes(client, bucket, bag_info_key).decode("utf-8")
    bag_info_body = patch_bag_info_external_description(
        bag_info_body,
        omeka_title=title,
        package_id=game_key,
        wasabi_uri=aip,
        submission_date=submission_date,
    )
    bag_info_bytes = bag_info_body.encode("utf-8")
    if not bag_info_body.endswith("\n"):
        bag_info_bytes += b"\n"

    tagmanifest = s3_get_bytes(client, bucket, tagmanifest_key).decode("utf-8")
    tagmanifest = update_manifest_digest(tagmanifest, "bag-info.txt", sha256_hex(bag_info_bytes))
    tagmanifest = update_manifest_digest(tagmanifest, "metadata/submission.json", sha256_hex(sub_bytes))
    tagmanifest_bytes = tagmanifest.encode("utf-8")

    plan = {
        "game_key": game_key,
        "bucket": bucket,
        "prefix": prefix,
        "files": ["metadata/submission.json", "bag-info.txt", "tagmanifest-sha256.txt"],
        "execute": execute,
    }
    if not execute:
        print(json.dumps(plan, indent=2))
        return plan

    for key, body in (
        (sub_key, sub_bytes),
        (bag_info_key, bag_info_bytes),
        (tagmanifest_key, tagmanifest_bytes),
    ):
        tmp = Path("/tmp") / f"press-catalog-patch-{hashlib.sha256(key.encode()).hexdigest()[:12]}"
        tmp.write_bytes(body)
        try:
            upload_file_to_s3(client, bucket=bucket, key=key, path=tmp)
            print(f"  uploaded s3://{bucket}/{key} ({len(body)} bytes)")
        finally:
            tmp.unlink(missing_ok=True)
    print(json.dumps({**plan, "status": "uploaded"}, indent=2))
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__
        + " Resolve ledger game_key from Omeka item id when needed (full package_id, not title slug)."
    )
    id_group = parser.add_mutually_exclusive_group(required=True)
    id_group.add_argument(
        "--game-key",
        help="Ledger game_key (full package_id, e.g. avengers-initiative-01a15345-…)",
    )
    id_group.add_argument(
        "--omeka-item-id",
        type=int,
        help="Look up game_key from game_ingest.omeka_item_id (preferred when correcting from an ARK)",
    )
    parser.add_argument("--title", required=True)
    parser.add_argument("--reference-uri", required=True)
    parser.add_argument("--former-title", required=True)
    parser.add_argument(
        "--correction-summary",
        default=(
            "Source press folder was mislabeled; package content matches SNK ADK official "
            "heritage press material."
        ),
    )
    parser.add_argument(
        "--description",
        help="Full dcterms:description (default: generated from title and reference URI)",
    )
    parser.add_argument("--execute", action="store_true", help="Upload bag metadata to Wasabi")
    args = parser.parse_args()

    description = (args.description or "").strip() or (
        f"Press material for {args.title}: SNK ADK developer heritage wallpapers and related "
        f"artwork (see official reference). "
        f"Catalog correction ({date.today().isoformat()}): former catalog title was "
        f"“{args.former_title}”; updated to match SNK’s ADK official page ({args.reference_uri})."
    )

    if args.omeka_item_id is not None:
        ledger = _ledger_row_by_omeka_item_id(args.omeka_item_id)
    else:
        ledger = _ledger_row(args.game_key)
    patch_omeka(
        item_id=int(ledger["omeka_item_id"]),
        title=args.title,
        description=description,
        reference_uri=args.reference_uri,
        former_title=args.former_title,
    )
    patch_wasabi_bag(
        ledger=ledger,
        title=args.title,
        reference_uri=args.reference_uri,
        former_title=args.former_title,
        correction_summary=args.correction_summary,
        execute=args.execute,
    )


if __name__ == "__main__":
    main()
