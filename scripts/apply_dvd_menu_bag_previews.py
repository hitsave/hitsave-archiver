#!/usr/bin/env python3
"""Backfill DVD menu JPEGs into BagIt metadata/hitsave on disk + Wasabi."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import bagit
    import psycopg
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from dip_package_previews import load_preview_config, write_dvd_menu_packaged_previews  # noqa: E402
from preservation_common import resolve_wasabi_bucket, sha256_file, wasabi_unpacked_prefix  # noqa: E402
from config_require import require_bool  # noqa: E402
from upload_bag_wasabi import (  # noqa: E402
    DEFAULT_INGEST_CONFIG,
    load_wasabi_settings,
    make_s3_client,
    object_key,
    pg_connect,
    resolve_config_path,
    upload_file_to_s3,
    verify_uploaded_object,
)
from yaml_config import load_yaml  # noqa: E402


@dataclass
class LedgerRow:
    game_key: str
    package_id: str
    package_storage_prefix: str
    omeka_item_id: int | None


def load_cfg(path: Path) -> dict:
    raw = load_yaml(path)
    return raw.get("dvd_menu_bag_previews") or {}


def slug_from_iso_name(iso_name: str) -> str:
    stem = Path(iso_name).stem
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-").lower()


def ready_folder_from_iso(iso_path: str) -> str:
    parts = Path(iso_path.replace("\\", "/")).parts
    if "ready" not in parts:
        raise ValueError(f"not an intake ready path: {iso_path}")
    idx = parts.index("ready")
    return parts[idx + 1]


def ledger_lookup(conn, ready_folder: str) -> LedgerRow | None:
    like_end = f"%/ready/{ready_folder}"
    like_mid = f"%/ready/{ready_folder}/%"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT game_key, package_id, package_storage_prefix, omeka_item_id
            FROM game_ingest
            WHERE ingest_kind = 'dump'
              AND status = 'complete'
              AND package_storage_prefix IS NOT NULL
              AND package_storage_prefix <> ''
              AND (source_path LIKE %s OR source_path LIKE %s)
            ORDER BY game_key
            LIMIT 1
            """,
            (like_end, like_mid),
        )
        row = cur.fetchone()
    if not row:
        return None
    return LedgerRow(
        game_key=str(row[0]),
        package_id=str(row[1] or row[0]),
        package_storage_prefix=str(row[2]).strip("/"),
        omeka_item_id=int(row[3]) if row[3] is not None else None,
    )


def collect_upload_relpaths(bag_dir: Path) -> list[str]:
    rels: set[str] = set()
    for name in ("manifest-sha256.txt", "tagmanifest-sha256.txt", "bagit.txt"):
        if (bag_dir / name).is_file():
            rels.add(name)
    hitsave = bag_dir / "metadata" / "hitsave"
    if hitsave.is_dir():
        for path in hitsave.rglob("*"):
            if path.is_file():
                rels.add(path.relative_to(bag_dir).as_posix())
    return sorted(rels)


def upload_bag_relpaths(
    bag_dir: Path,
    package_storage_prefix: str,
    rel_paths: list[str],
    *,
    ingest_config_path: Path,
) -> None:
    wasabi, _ = load_wasabi_settings(ingest_config_path)
    config_root = ingest_config_path.parent.parent
    creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), config_root))
    client = make_s3_client(wasabi, creds)
    bucket = resolve_wasabi_bucket(wasabi, "dump")
    storage_root = wasabi_unpacked_prefix(package_storage_prefix)
    verify = require_bool(wasabi, "verify_upload", where="ingest.yaml wasabi")

    for rel in rel_paths:
        path = bag_dir / rel
        if not path.is_file():
            continue
        key = object_key(storage_root, bag_dir, path)
        size = path.stat().st_size
        digest = sha256_file(path) if verify else None
        upload_file_to_s3(client, bucket=bucket, key=key, path=path)
        verify_uploaded_object(
            client, bucket=bucket, key=key, size=size, local_digest=digest
        )
        print(f"  uploaded s3://{bucket}/{key}", flush=True)


def patch_bag(bag_dir: Path, menu_jpg: Path, package_id: str) -> list[str]:
    if not (bag_dir / "bagit.txt").is_file():
        raise FileNotFoundError(f"not a bag: {bag_dir}")
    preview_cfg = load_preview_config()
    write_dvd_menu_packaged_previews(
        bag_dir,
        menu_jpg=menu_jpg,
        package_id=package_id,
        config=preview_cfg,
    )
    bag = bagit.Bag(str(bag_dir))
    bag.save(manifests=True)
    return collect_upload_relpaths(bag_dir)


def main() -> None:
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/config/preservation/dvd-menu-bag-previews.yaml")
    cfg = load_cfg(cfg_path)
    report_path = Path(cfg["run_report_path"])
    preview_root = Path(cfg["preview_root"])
    bag_root = Path(cfg["bag_root"])
    ingest_config = Path(cfg.get("ingest_config") or DEFAULT_INGEST_CONFIG)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    packages = report.get("packages") or []

    ingest = load_yaml(ingest_config)
    db_cfg = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]

    ok_status = ("ok", "ok_low_score")
    updated: list[str] = []
    skipped: list[tuple[str, str]] = []

    print(
        f"DVD menu bag apply: {len(packages)} report entries, report={report_path}",
        flush=True,
    )

    with pg_connect(db_cfg) as conn:
        for pkg in packages:
            status = str(pkg.get("status") or "")
            if status not in ok_status:
                skipped.append((str(pkg.get("iso")), f"status={status}"))
                continue
            iso = str(pkg.get("iso") or "")
            try:
                folder = ready_folder_from_iso(iso)
            except ValueError as exc:
                skipped.append((iso, str(exc)))
                continue
            row = ledger_lookup(conn, folder)
            if row is None:
                skipped.append((iso, f"no ledger row for {folder}"))
                continue
            slug = slug_from_iso_name(Path(iso).name)
            menu_jpg = preview_root / slug / "menu_preview.jpg"
            if not menu_jpg.is_file():
                skipped.append((iso, f"missing preview {menu_jpg}"))
                continue
            bag_dir = bag_root / row.game_key
            if not bag_dir.is_dir():
                skipped.append((iso, f"no local bag {bag_dir}"))
                continue
            print(f"=== {row.game_key} ===", flush=True)
            try:
                rels = patch_bag(bag_dir, menu_jpg, row.package_id)
                print(f"  patched bag ({len(rels)} files to upload)", flush=True)
            except OSError as exc:
                skipped.append((iso, str(exc)))
                print(f"  FAIL patch: {exc}", flush=True)
                continue
            if cfg.get("upload_wasabi", True):
                upload_bag_relpaths(
                    bag_dir,
                    row.package_storage_prefix,
                    rels,
                    ingest_config_path=ingest_config,
                )
            updated.append(row.game_key)
            print(f"  done ({len(updated)} updated)", flush=True)

    summary_path = Path(
        cfg.get("summary_path") or "/output/dvd-menu-bag-apply-summary.json"
    )
    summary = {
        "updated_game_keys": updated,
        "skipped": [{"iso": a, "reason": b} for a, b in skipped],
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nPatched {len(updated)} bags; skipped {len(skipped)}", flush=True)
    if skipped:
        for iso, reason in skipped[:20]:
            print(f"  skip: {iso} ({reason})", flush=True)
        if len(skipped) > 20:
            print(f"  ... and {len(skipped) - 20} more skips", flush=True)
    print(f"Summary: {summary_path}", flush=True)

    omeka_script = cfg.get("omeka_register_script")
    if omeka_script and cfg.get("refresh_omeka", False) and updated:
        script = Path(omeka_script)
        if not script.is_file():
            print(f"Omeka script missing: {script}", file=sys.stderr)
            sys.exit(1)
        for game_key in updated:
            print(f"--- omeka refresh {game_key} ---")
            subprocess.run(
                [str(script), "--game-key", game_key, "--force"],
                check=False,
            )


if __name__ == "__main__":
    main()
