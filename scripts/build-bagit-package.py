#!/usr/bin/env python3
"""Build RFC 8493 BagIt package (payload under data/) for OAIS AIP/DIP."""
from __future__ import annotations

import json
import mimetypes
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bagit  # noqa: E402

from package_identity import PackageIdentity  # noqa: E402
from dump_classify import DumpClassification, classify_dump_sip  # noqa: E402
from preservation_common import (  # noqa: E402
    bagit_package_s3_uri_for_prefix,
    iso_now,
    load_ingest_config,
    load_packaging_config,
    resolve_game_config,
    sha256_file,
    should_skip,
    wasabi_bagit_unpacked_prefix,
)
from ingest_kind import storage_profile_for_kind  # noqa: E402
from submission_sources import resolve_submission_source  # noqa: E402
from dip_package_previews import load_preview_config, write_hitsave_previews  # noqa: E402

INVENTORY_VERSION = 1


@dataclass
class FileRow:
    rel: str
    path: Path
    size: int
    sha256: str
    mime: str


def load_game_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def collect_files(source: Path, max_files: int, max_bytes: int) -> list[FileRow]:
    rows: list[FileRow] = []
    total = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file() or should_skip(path):
            continue
        size = path.stat().st_size
        total += size
        if len(rows) >= max_files:
            raise SystemExit(f"Would exceed max_files ({max_files})")
        if total > max_bytes:
            raise SystemExit(f"Would exceed max_total_bytes ({max_bytes})")
        rel = path.relative_to(source).as_posix()
        mime, _ = mimetypes.guess_type(path.name)
        rows.append(
            FileRow(
                rel=rel,
                path=path,
                size=size,
                sha256=sha256_file(path),
                mime=mime or "application/octet-stream",
            )
        )
    if not rows:
        raise SystemExit(f"No files under {source}")
    return rows


def build_inventory(rows: list[FileRow], payload_dir: str) -> dict:
    files = []
    ext_counts: Counter[str] = Counter()
    total_bytes = 0
    for rec in rows:
        data_path = f"{payload_dir}/{rec.rel}"
        ext = Path(rec.rel).suffix.lower().lstrip(".") or "(none)"
        ext_counts[ext] += 1
        total_bytes += rec.size
        files.append(
            {
                "path": data_path,
                "size_bytes": rec.size,
                "sha256": rec.sha256,
                "mime": rec.mime,
            }
        )
    return {
        "manifest_version": INVENTORY_VERSION,
        "files": files,
        "summary": {
            "file_count": len(files),
            "total_bytes": total_bytes,
            "by_extension": dict(sorted(ext_counts.items())),
        },
    }


def validate_bag(bag_dir: Path) -> None:
    bag = bagit.Bag(str(bag_dir))
    bag.validate()


def build_bagit_package(
    source: Path,
    bag_dir: Path,
    *,
    identity: PackageIdentity,
    agent_version: str,
    repository_code: str,
    max_files: int,
    max_bytes: int,
    ingest_kind: str = "press_material",
    dump_classification: DumpClassification | None = None,
) -> tuple[dict, dict]:
    pkg = load_packaging_config()
    bag_cfg = pkg.get("bagit") or {}
    payload_dir = str(bag_cfg.get("payload_dir") or "data")
    meta_dir = str(bag_cfg.get("metadata_dir") or "metadata")

    if bag_dir.exists():
        shutil.rmtree(bag_dir)
    bag_dir.mkdir(parents=True)

    rows = collect_files(source, max_files, max_bytes)
    # Stage payload at bag root; bagit.make_bag moves files into data/ (RFC 8493 payload).
    for rec in rows:
        dest = bag_dir / rec.rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rec.path, dest)

    ingested_at = iso_now()
    submission = resolve_submission_source(source)
    submission_id = str(submission.get("id") or "") if submission else ""
    submission_date = str(submission.get("submission_date") or "") if submission else ""

    ingest_cfg = load_ingest_config()
    wasabi = ingest_cfg.get("wasabi") or {}
    bucket = str(wasabi.get("bucket") or "hitsave-incoming")
    if storage_profile_for_kind(ingest_kind) == "bagit_unpacked" and dump_classification:
        wasabi_key = dump_classification.package_storage_prefix
    else:
        prefix = str(wasabi.get("prefix") or "press-material").strip("/")
        wasabi_key = wasabi_bagit_unpacked_prefix(
            prefix,
            package_id=identity.package_id,
            sort_letter=identity.sort_letter,
        )
    wasabi_uri = bagit_package_s3_uri_for_prefix(bucket, wasabi_key)

    submission_json = {
        "submission_source_id": submission_id,
        "submission_type": submission.get("type") if submission else None,
        "submission_date": submission_date or None,
        "sip_path": str(source),
        "relative_sip_path": identity.relative_sip_path,
        "package_id": identity.package_id,
        "package_uuid": identity.package_uuid,
        "sip_fingerprint": identity.sip_fingerprint,
        "title_slug": identity.title_slug,
        "sort_letter": identity.sort_letter,
        "omeka_item_title": identity.omeka_item_title,
        "wasabi_key": wasabi_key,
        "package_storage_prefix": wasabi_key,
        "storage_layout": storage_profile_for_kind(ingest_kind),
        "game_key": identity.package_id,
        "ingested_at": ingested_at,
        "repository_code": repository_code,
    }
    if dump_classification:
        submission_json.update(
            {
                "dump_class": dump_classification.dump_class,
                "dump_system": dump_classification.dump_system,
                "dump_category": dump_classification.dump_category,
                "package_storage_prefix": dump_classification.package_storage_prefix,
            }
        )
    inventory = build_inventory(rows, payload_dir)

    bag_info: dict[str, str] = {
        "Bag-Software": "HitSave build-bagit-package.py",
        "Bag-Software-Version": agent_version,
    }
    bag_info["External-Identifier"] = identity.external_identifier()
    bag_info["Internal-Sender-Identifier"] = identity.relative_sip_path
    if submission_id:
        bag_info["Bag-Group-Identifier"] = submission_id
    bag_info["External-Description"] = (
        f"{identity.omeka_item_title}; package_id={identity.package_id}; "
        f"wasabi={wasabi_uri}; submission_date={submission_date or ''}"
    )

    bagit.make_bag(str(bag_dir), bag_info=bag_info, checksums=["sha256"])

    meta_root = bag_dir / meta_dir
    meta_root.mkdir(parents=True, exist_ok=True)
    (meta_root / "submission.json").write_text(
        json.dumps(submission_json, indent=2) + "\n", encoding="utf-8"
    )
    (meta_root / "inventory.json").write_text(
        json.dumps(inventory, indent=2) + "\n", encoding="utf-8"
    )
    preview_cfg = load_preview_config()
    preview_manifest = write_hitsave_previews(
        bag_dir,
        package_id=identity.package_id,
        payload_dir=payload_dir,
        metadata_dir=meta_dir,
        config=preview_cfg,
    )
    if preview_manifest:
        print(f"  Packaged previews: {len(preview_manifest.get('grid', {}))} grid thumbs")
    bag = bagit.Bag(str(bag_dir))
    bag.save(manifests=True)
    validate_bag(bag_dir)
    return inventory, submission_json


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: build-bagit-package.py <game_config.yaml>")

    cfg_path = Path(sys.argv[1]).resolve()
    raw = load_game_config(cfg_path)
    ingest_cfg = load_ingest_config()
    press_root = Path(ingest_cfg["paths"]["press_material_root"])
    if raw.get("source_subpath") and not raw.get("source_game_folder"):
        sub = str(raw["source_subpath"]).strip().strip("/")
        raw["source_game_folder"] = str(press_root / sub)
    source = Path(raw["source_game_folder"]).resolve()
    if not source.is_dir():
        raise SystemExit(f"Source not found: {source}")

    import psycopg

    from package_ledger import resolve_package_identity  # noqa: E402

    db_cfg = yaml.safe_load(Path(ingest_cfg["database"]["config_file"]).read_text())["postgres"]
    title = str(raw.get("omeka_item_title") or source.name)
    with psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    ) as conn:
        identity = resolve_package_identity(
            conn,
            source=source,
            press_material_root=press_root,
            omeka_item_title=title,
        )
    raw["game_key"] = identity.package_id
    cfg = resolve_game_config(raw, agent_version="build-bagit-package.py")
    ingest_kind = str(cfg.get("ingest_kind") or "press_material")
    dump_clf = None
    if ingest_kind == "dump":
        dump_clf = classify_dump_sip(source, package_id=identity.package_id)
    bag_dir = Path(cfg["bagit_bag_dir"]).resolve()
    pres = cfg.get("preservation") or {}
    max_files = int(cfg["max_files"])
    max_bytes = int(cfg["max_total_bytes"])

    inventory, _submission = build_bagit_package(
        source,
        bag_dir,
        identity=identity,
        agent_version=str(pres.get("agent_version", "build-bagit-package.py")),
        repository_code=str(pres.get("repository_code", "hitsave")),
        max_files=max_files,
        max_bytes=max_bytes,
        ingest_kind=ingest_kind,
        dump_classification=dump_clf,
    )

    manifest = bag_dir / "manifest-sha256.txt"
    print(f"BagIt package: {bag_dir}")
    print(f"  Manifest: {manifest}")
    print(f"  Files: {inventory['summary']['file_count']}")


if __name__ == "__main__":
    main()
