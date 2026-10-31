#!/usr/bin/env python3
"""Backfill DVD menu JPEGs into BagIt metadata/hitsave on Wasabi (and the local bag if present).

Tag-only update: writes the preview files and updates ONLY their lines in
tagmanifest-sha256.txt. The payload manifest (manifest-sha256.txt, the ingest-time fixity
baseline) and bag-info.txt are never regenerated. The previous version ran
bag.save(manifests=True) on the local copy, re-hashing the payload from local disk and
uploading that manifest over Wasabi's, which would bless any local corruption and broke
Wasabi bags whose tag files had been updated there (renames).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    import psycopg
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from dip_package_previews import load_preview_config, write_dvd_menu_packaged_previews  # noqa: E402
from preservation_common import resolve_wasabi_bucket, wasabi_unpacked_prefix  # noqa: E402
from s3_etag import hash_file  # noqa: E402
from config_require import require_bool  # noqa: E402
from upload_bag_wasabi import (  # noqa: E402
    DEFAULT_INGEST_CONFIG,
    load_wasabi_settings,
    make_s3_client,
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


def upsert_manifest_lines(manifest_text: str, digests: dict[str, str]) -> str:
    """Replace or append `<sha256>  <path>` lines for the given paths; keep every other line."""
    remaining = dict(digests)
    lines: list[str] = []
    for line in manifest_text.splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ValueError(f"Unexpected manifest line: {line!r}")
        path = parts[1].strip()
        if path in remaining:
            lines.append(f"{remaining.pop(path)}  {path}")
        else:
            lines.append(line)
    lines.extend(f"{digest}  {path}" for path, digest in sorted(remaining.items()))
    return "\n".join(lines) + "\n"


def build_preview_files(menu_jpg: Path, package_id: str, work: Path) -> dict[str, Path]:
    """Render the DVD-menu previews into a scratch dir; {bag-relative path: file}."""
    preview_cfg = load_preview_config()
    write_dvd_menu_packaged_previews(work, menu_jpg=menu_jpg, package_id=package_id, config=preview_cfg)
    hitsave = work / "metadata" / "hitsave"
    return {
        p.relative_to(work).as_posix(): p for p in sorted(hitsave.rglob("*")) if p.is_file()
    }


def apply_to_wasabi(
    files: dict[str, Path],
    package_storage_prefix: str,
    *,
    ingest_config_path: Path,
    s3=None,
) -> None:
    """Upload preview files (verified), then a tagmanifest with only their lines changed."""
    wasabi, _ = load_wasabi_settings(ingest_config_path)
    config_root = ingest_config_path.parent.parent
    if s3 is None:
        creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), config_root))
        s3 = make_s3_client(wasabi, creds)
    bucket = resolve_wasabi_bucket(wasabi, "dump")
    root = wasabi_unpacked_prefix(package_storage_prefix)
    verify = require_bool(wasabi, "verify_upload", where="ingest.yaml wasabi")

    tag_key = f"{root}tagmanifest-sha256.txt"
    tagmanifest = s3.get_object(Bucket=bucket, Key=tag_key)["Body"].read().decode("utf-8")
    if not tagmanifest.strip():
        raise RuntimeError(f"empty tagmanifest at s3://{bucket}/{tag_key}")

    digests: dict[str, str] = {}
    for rel, path in files.items():
        hashes = hash_file(path)
        digests[rel] = hashes.sha256
        key = f"{root}{rel}"
        upload_file_to_s3(s3, bucket=bucket, key=key, path=path)
        verify_uploaded_object(
            s3, bucket=bucket, key=key, size=hashes.size,
            local_digest=hashes.sha256 if verify else None,
            local_hashes=hashes if verify else None,
        )
        print(f"  uploaded s3://{bucket}/{key}", flush=True)

    new_tag = upsert_manifest_lines(tagmanifest, digests)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
        tmp.write(new_tag)
    tmp_path = Path(tmp.name)
    try:
        hashes = hash_file(tmp_path)
        upload_file_to_s3(s3, bucket=bucket, key=tag_key, path=tmp_path)
        verify_uploaded_object(
            s3, bucket=bucket, key=tag_key, size=hashes.size,
            local_digest=hashes.sha256, local_hashes=hashes,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    print(f"  updated {len(digests)} line(s) in s3://{bucket}/{tag_key}", flush=True)


def apply_to_local_bag(files: dict[str, Path], bag_dir: Path) -> bool:
    """Same tag-only update on the local bag so it doesn't drift from Wasabi."""
    tag_path = bag_dir / "tagmanifest-sha256.txt"
    if not tag_path.is_file():
        return False
    digests = {}
    for rel, path in files.items():
        dest = bag_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        digests[rel] = hash_file(dest).sha256
    tag_path.write_text(upsert_manifest_lines(tag_path.read_text(encoding="utf-8"), digests), encoding="utf-8")
    return True


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
            print(f"=== {row.game_key} ===", flush=True)
            with tempfile.TemporaryDirectory() as work:
                try:
                    files = build_preview_files(menu_jpg, row.package_id, Path(work))
                except (OSError, RuntimeError) as exc:
                    skipped.append((iso, str(exc)))
                    print(f"  FAIL previews: {exc}", flush=True)
                    continue
                if cfg.get("upload_wasabi", True):
                    apply_to_wasabi(files, row.package_storage_prefix, ingest_config_path=ingest_config)
                bag_dir = bag_root / row.game_key
                if apply_to_local_bag(files, bag_dir):
                    print(f"  local bag tagmanifest updated: {bag_dir}", flush=True)
                else:
                    print(f"  (no local bag at {bag_dir}; Wasabi only)", flush=True)
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
