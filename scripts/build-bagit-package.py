#!/usr/bin/env python3
"""Build RFC 8493 BagIt package (payload under data/) + plain .tar for OAIS AIP/DIP."""
from __future__ import annotations

import json
import mimetypes
import shutil
import sys
import tarfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bagit  # noqa: E402

from preservation_common import (  # noqa: E402
    iso_now,
    load_packaging_config,
    resolve_game_config,
    sha256_file,
    should_skip,
)
from submission_sources import resolve_submission_source  # noqa: E402

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


def tar_bag_directory(bag_dir: Path, tar_path: Path) -> str:
    if tar_path.exists():
        tar_path.unlink()
    tar_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "w") as tar:
        for path in sorted(bag_dir.rglob("*")):
            if path.is_file():
                arc = path.relative_to(bag_dir).as_posix()
                tar.add(path, arcname=arc)
    return sha256_file(tar_path)


def build_bagit_package(
    source: Path,
    bag_dir: Path,
    tar_path: Path,
    *,
    game_key: str,
    agent_version: str,
    repository_code: str,
    max_files: int,
    max_bytes: int,
) -> tuple[dict, str, dict]:
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

    submission_json = {
        "submission_source_id": submission_id,
        "submission_type": submission.get("type") if submission else None,
        "submission_date": submission_date or None,
        "sip_path": str(source),
        "game_key": game_key,
        "ingested_at": ingested_at,
        "repository_code": repository_code,
    }
    inventory = build_inventory(rows, payload_dir)

    bag_info: dict[str, str] = {
        "Bag-Software": "HitSave build-bagit-package.py",
        "Bag-Software-Version": agent_version,
    }
    if submission:
        if submission.get("bag_info_external_identifier"):
            bag_info["External-Identifier"] = str(submission["bag_info_external_identifier"])
        bag_info["External-Description"] = (
            f"game_key={game_key}; sip={source}; "
            f"submission_date={submission.get('submission_date', '')}"
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
    bag = bagit.Bag(str(bag_dir))
    bag.save(manifests=True)
    validate_bag(bag_dir)
    tar_sha256 = tar_bag_directory(bag_dir, tar_path)
    return inventory, tar_sha256, submission_json


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: build-bagit-package.py <game_config.yaml>")

    cfg_path = Path(sys.argv[1]).resolve()
    cfg = resolve_game_config(load_game_config(cfg_path), agent_version="build-bagit-package.py")
    source = Path(cfg["source_game_folder"]).resolve()
    if not source.is_dir():
        raise SystemExit(f"Source not found: {source}")

    game_key = cfg["game_key"]
    bag_dir = Path(cfg["bagit_bag_dir"]).resolve()
    tar_path = Path(cfg["output_tar"]).resolve()
    pres = cfg.get("preservation") or {}
    max_files = int(cfg["max_files"])
    max_bytes = int(cfg["max_total_bytes"])

    inventory, tar_sha256, _submission = build_bagit_package(
        source,
        bag_dir,
        tar_path,
        game_key=game_key,
        agent_version=str(pres.get("agent_version", "build-bagit-package.py")),
        repository_code=str(pres.get("repository_code", "hitsave")),
        max_files=max_files,
        max_bytes=max_bytes,
    )

    print(f"BagIt package: {bag_dir}")
    print(f"  Tar (AIP/DIP): {tar_path}")
    print(f"  Tar SHA-256: {tar_sha256}")
    print(f"  Files: {inventory['summary']['file_count']}")


if __name__ == "__main__":
    main()
