#!/usr/bin/env python3
"""Verify ledger-complete Drive Redump packages and move them to sibling processed/."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GDRIVE_ROOT = REPO_ROOT.parent / "hitsave-gdrive-intake"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
_gdrive_src = GDRIVE_ROOT / "src"
if _gdrive_src.is_dir():
    sys.path.insert(0, str(_gdrive_src))
elif Path("/app/intake").is_dir():
    sys.path.insert(0, "/app")

import boto3  # noqa: E402
import yaml  # noqa: E402
from botocore.config import Config  # noqa: E402

from intake.gdrive.client import (  # noqa: E402
    build_drive_service,
    find_child_folder_by_name,
    get_file_metadata,
    list_children,
    move_drive_folder,
    resolve_credentials_path,
)
from intake.gdrive_poll import run_poll  # noqa: E402
from upload_bag_wasabi import load_wasabi_settings, pg_connect  # noqa: E402
from yaml_config import load_yaml  # noqa: E402
from preservation_common import wasabi_s3_region  # noqa: E402


def slug_from_folder_name(name: str, *, prefix: str = "redump") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    return f"{prefix}-{slug}"


def ledger_row(db_cfg: dict, slug: str) -> tuple[str, int | None, str] | None:
    slug_sql = slug.replace("'", "''")
    q = (
        "SELECT status, omeka_item_id, COALESCE(package_storage_prefix, '') "
        "FROM game_ingest WHERE ingest_kind='dump' "
        f"AND (source_path LIKE '%/{slug_sql}' OR source_path LIKE '%/{slug_sql}/%') "
        "ORDER BY (status = 'complete') DESC LIMIT 1"
    )
    with pg_connect(db_cfg) as conn:
        row = conn.execute(q).fetchone()
    if not row:
        return None
    return str(row[0]), row[1], str(row[2])


DISC_IMAGE_SUFFIXES = (".iso", ".bin", ".img", ".chd", ".gdi")


def drive_folder_has_disc_image(pkg: dict) -> bool:
    files = pkg.get("files") or []
    if not files:
        return False
    for f in files:
        name = str(f.get("name") or "").lower()
        if any(name.endswith(suffix) for suffix in DISC_IMAGE_SUFFIXES):
            return True
    return False


def has_bag_on_dumps(s3, prefix: str) -> bool:
    if not prefix.strip():
        return False
    p = prefix.strip("/") + "/"
    resp = s3.list_objects_v2(Bucket="hitsave-dumps", Prefix=p + "bagit.txt", MaxKeys=1)
    return bool(resp.get("Contents"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-root", type=Path, default=Path("/config"))
    parser.add_argument("--ingest-config", type=Path, default=Path("/config/preservation/ingest.yaml"))
    parser.add_argument("--poll-json", type=Path, help="Use saved poll output instead of live Drive list")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--create-processed-folder", action="store_true")
    args = parser.parse_args()

    gdrive_doc = yaml.safe_load((args.config_root / "gdrive.yaml").read_text(encoding="utf-8")) or {}
    gdrive_cfg = gdrive_doc.get("gdrive") or {}
    inbox_id = str(gdrive_cfg.get("folder_id") or "").strip()
    processed_name = str(gdrive_cfg.get("processed_sibling_folder_name") or "processed").strip()
    if not inbox_id:
        raise SystemExit("gdrive.folder_id required")

    cred_path = resolve_credentials_path(
        str(gdrive_cfg.get("credentials_file", "secrets/gdrive-service-account.json")),
        args.config_root,
    )
    service = build_drive_service(cred_path, read_write=args.execute) if args.execute else None

    if args.poll_json:
        import json

        poll = json.loads(args.poll_json.read_text(encoding="utf-8"))
    else:
        if service is None:
            service = build_drive_service(cred_path, read_write=False)
        poll = run_poll(gdrive_cfg, list_files_in_packages=False)

    packages = poll.get("packages") or []
    print(f"Drive inbox {poll.get('folder_name')!r} ({inbox_id}): {len(packages)} package folder(s)")

    ingest = load_yaml(args.ingest_config)
    db_cfg = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    wasabi, _ = load_wasabi_settings(args.ingest_config)
    wasabi_creds = load_yaml(Path("/config/secrets/wasabi-credentials.yaml"))
    s3 = boto3.client(
        "s3",
        endpoint_url=str(wasabi["endpoint"]).rstrip("/"),
        region_name=wasabi_s3_region(wasabi),
        aws_access_key_id=str(wasabi_creds["access_key_id"]),
        aws_secret_access_key=str(wasabi_creds["secret_access_key"]),
        config=Config(signature_version="s3v4"),
    )

    ready_to_move: list[tuple[str, str, str]] = []
    pending: list[str] = []

    for pkg in packages:
        fid = str(pkg.get("id") or "")
        name = str(pkg.get("name") or "")
        if not fid or not name:
            continue
        if name == processed_name:
            continue
        slug = slug_from_folder_name(name)
        row = ledger_row(db_cfg, slug)
        if not row:
            pending.append(f"{name} (no ledger row; slug={slug})")
            continue
        status, omeka_id, prefix = row
        if status != "complete" or not omeka_id:
            pending.append(f"{name} (ledger={status}, omeka={omeka_id})")
            continue
        if not has_bag_on_dumps(s3, prefix):
            pending.append(f"{name} (no bagit.txt on hitsave-dumps for {prefix})")
            continue
        if not drive_folder_has_disc_image(pkg):
            pending.append(
                f"{name} (Drive folder incomplete — no disc image; preserved via Wasabi; leave on Drive)"
            )
            continue
        ready_to_move.append((fid, name, slug))
        print(f"  OK {name[:60]}… → Omeka {omeka_id}")

    print(f"\nReady to move: {len(ready_to_move)}; not ready: {len(pending)}")
    for line in pending:
        print(f"  SKIP {line}")

    if not args.execute:
        print("\nDry-run. Re-run with --execute to move folders on Drive.")
        return

    if service is None:
        raise SystemExit("--execute requires Drive credentials mounted at /secrets/")

    configured_processed = str(gdrive_cfg.get("processed_folder_id") or "").strip()
    if configured_processed:
        processed_id = configured_processed
        print(f"processed/ folder id (from config): {processed_id}")
    else:
        inbox_meta = get_file_metadata(service, inbox_id)
        parents = inbox_meta.get("parents") or []
        if parents:
            parent_id = str(parents[0])
            processed_id = find_child_folder_by_name(
                service,
                parent_id,
                processed_name,
                create_if_missing=args.create_processed_folder,
            )
            print(f"processed/ folder id (sibling of inbox): {processed_id}")
        else:
            processed_id = find_child_folder_by_name(
                service,
                inbox_id,
                processed_name,
                create_if_missing=args.create_processed_folder,
            )
            print(
                f"processed/ folder id (inside inbox; service account has no parent on shared drive): "
                f"{processed_id}"
            )

    moved = 0
    for fid, name, slug in ready_to_move:
        print(f"MOVE {name!r} …", flush=True)
        move_drive_folder(service, fid, processed_id)
        moved += 1
    print(f"Moved {moved} folder(s) into {processed_name}/")


if __name__ == "__main__":
    main()
