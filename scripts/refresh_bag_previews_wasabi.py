#!/usr/bin/env python3
"""Rebuild metadata/hitsave previews (grid + display JPEGs, scans, manifest) for bags on Wasabi.

For bags packaged before display JPEGs / uncapped scans existed. Tag-only update, like
apply_dvd_menu_bag_previews.py: uploads the preview files (verified) and changes ONLY their lines
in tagmanifest-sha256.txt. Payload, manifest-sha256.txt and bag-info.txt are never touched.

Bags whose manifest has previews not derived from payload (DVD "menu" extracted from the ISO)
keep them: the rebuilt payload previews (scans) are merged in after them, and the existing browse
collages stay as they are (not regenerated or uploaded).

Only gallery-eligible images are downloaded. Every other payload file becomes a sparse
placeholder of its real size, so the preview keys (f<N>, position in the sorted payload listing)
match the keys the bag was packaged with and the viewer's index.

After uploading, the item's omeka_dip_package media gets data.previews_version = <UTC timestamp>
(omeka-dip-viewer >= 0.5.11 keys its index cache on it), so the item page shows the new previews
right away. Data-only media PATCH; item values are not touched.

Usage:
  refresh_bag_previews_wasabi.py [--execute] <game_key> [game_key ...]
Without --execute: builds the previews locally and prints what would change (no uploads).
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
from datetime import datetime, timezone
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from apply_dvd_menu_bag_previews import apply_to_wasabi  # noqa: E402
from dip_package_previews import (  # noqa: E402
    IndexedPayloadFile,
    _gallery_eligible,
    load_preview_config,
    write_hitsave_previews,
)
from preservation_common import resolve_wasabi_bucket, wasabi_unpacked_prefix  # noqa: E402
from upload_bag_wasabi import (  # noqa: E402
    DEFAULT_INGEST_CONFIG,
    load_wasabi_settings,
    make_s3_client,
    pg_connect,
    resolve_config_path,
)
from yaml_config import load_yaml  # noqa: E402


def ledger_rows(game_keys: list[str], ingest_cfg: dict) -> dict[str, tuple[str, int | None]]:
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    with pg_connect(db_cfg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT game_key, package_storage_prefix, omeka_media_id FROM game_ingest "
            "WHERE game_key = ANY(%s) AND status = 'complete' AND ingest_kind = 'dump'",
            (game_keys,),
        )
        return {key: (prefix, media_id) for key, prefix, media_id in cur.fetchall()}


def bump_previews_version(media_id: int, version: str) -> None:
    """data.previews_version on the gallery media (data-only PATCH; no item values involved)."""
    import requests

    from omeka_catalog_common import load_register_dip_omeka_api
    from omeka_staff_metadata import load_omeka_catalog_config, require_omeka_api_config

    config_root = Path(os.environ.get("HITSAVE_CONFIG_ROOT", "/config"))
    api_cfg = require_omeka_api_config(load_omeka_catalog_config(config_root))
    cred_path = config_root / Path(*Path(api_cfg["credentials_file"]).parts[1:])
    if not cred_path.is_file():
        cred_path = config_root / "secrets" / Path(api_cfg["credentials_file"]).name
    creds = load_yaml(cred_path)
    api = load_register_dip_omeka_api()
    resp = requests.patch(
        api.api_url(api_cfg["base_url"].rstrip("/"), f"media/{media_id}", creds),
        headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
        json={"data": {"previews_version": version}},
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"PATCH media {media_id} previews_version failed ({resp.status_code}): {resp.text[:500]}")


def payload_objects(s3, bucket: str, root: str) -> dict[str, int]:
    """data/... relative path -> size, from the bag on Wasabi."""
    out: dict[str, int] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"{root}data/"):
        for obj in page.get("Contents", []):
            out[obj["Key"][len(root):]] = int(obj["Size"])
    return out


def existing_manifest(s3, bucket: str, root: str) -> dict | None:
    try:
        body = s3.get_object(Bucket=bucket, Key=f"{root}metadata/hitsave/preview-manifest.json")["Body"].read()
    except s3.exceptions.NoSuchKey:
        return None
    return json.loads(body)


def foreign_preview_keys(manifest: dict | None) -> list[str]:
    """Preview keys not derived from payload images (e.g. DVD "menu" from the ISO): a rebuild from
    payload alone would drop them, so they are merged back in (merge_foreign_previews)."""
    if not manifest:
        return []
    keys = set(manifest.get("grid") or {}) | set(manifest.get("gallery_file_keys") or [])
    return sorted(k for k in keys if not re.fullmatch(r"f\d+", str(k)))


def merge_foreign_previews(old: dict, new: dict, foreign: list[str]) -> dict:
    """New payload manifest + the old manifest's foreign previews (first in the gallery) and browse."""
    merged = dict(new)
    merged["grid"] = {**{k: old["grid"][k] for k in foreign if k in (old.get("grid") or {})}, **new.get("grid", {})}
    old_display = old.get("display") or {}
    merged["display"] = {**{k: old_display[k] for k in foreign if k in old_display}, **new.get("display", {})}
    merged["gallery_file_keys"] = [k for k in foreign if k in merged["grid"]] + [
        k for k in new.get("gallery_file_keys", []) if k not in foreign
    ]
    if old.get("browse"):
        merged["browse"] = old["browse"]
    return merged


def stage_bag(s3, bucket: str, root: str, objects: dict[str, int], work: Path, cfg: dict) -> int:
    """Scratch bag: real eligible images, sparse placeholders for the rest. Returns bytes fetched."""
    fetched = 0
    for rel, size in sorted(objects.items()):
        dest = work / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        probe = IndexedPayloadFile(
            key="", relative_path=rel[len("data/"):], path=dest, size=size,
            mime=mimetypes.guess_type(dest.name)[0] or "application/octet-stream",
        )
        if _gallery_eligible(probe, cfg):
            s3.download_file(bucket, f"{root}{rel}", str(dest))
            fetched += size
        else:
            with open(dest, "wb") as fh:
                fh.truncate(size)
    return fetched


def main() -> None:
    args = sys.argv[1:]
    execute = "--execute" in args
    keys = [a for a in args if not a.startswith("--")]
    if not keys:
        raise SystemExit(__doc__)
    ingest_path = DEFAULT_INGEST_CONFIG
    ingest_cfg = load_yaml(ingest_path)
    wasabi, _ = load_wasabi_settings(ingest_path)
    creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), ingest_path.parent.parent))
    s3 = make_s3_client(wasabi, creds)
    bucket = resolve_wasabi_bucket(wasabi, "dump")
    cfg = load_preview_config()
    rows = ledger_rows(keys, ingest_cfg)
    results = []
    for key in keys:
        if key not in rows:
            results.append({"game_key": key, "skipped": "no complete dump ledger row"})
            continue
        prefix, media_id = rows[key]
        root = wasabi_unpacked_prefix(prefix)
        package_id = prefix.rstrip("/").rsplit("/", 1)[-1]
        old_manifest = existing_manifest(s3, bucket, root)
        foreign = foreign_preview_keys(old_manifest)
        with tempfile.TemporaryDirectory(prefix="bag-previews-") as tmp:
            work = Path(tmp)
            objects = payload_objects(s3, bucket, root)
            fetched = stage_bag(s3, bucket, root, objects, work, cfg)
            manifest = write_hitsave_previews(work, package_id=package_id, config=cfg)
            if not manifest:
                results.append({"game_key": key, "skipped": "no gallery images"})
                continue
            hitsave = work / "metadata" / "hitsave"
            if foreign:
                # Keep the ISO-derived previews and their browse collages; add the payload scans.
                manifest = merge_foreign_previews(old_manifest or {}, manifest, foreign)
                for collage in hitsave.glob("browse-*.png"):
                    collage.unlink()
                manifest_path = work / str(cfg.get("manifest_inner_path") or "metadata/hitsave/preview-manifest.json")
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            files = {p.relative_to(work).as_posix(): p for p in sorted(hitsave.rglob("*")) if p.is_file()}
            summary = {
                "game_key": key,
                "gallery": len(manifest["gallery_file_keys"]),
                "scans": sum(1 for f in manifest["files"].values() if f["scan"]),
                "display": len(manifest["display"]),
                "downloaded_bytes": fetched,
                "files": len(files),
                "kept_previews": foreign,
            }
            if execute:
                apply_to_wasabi(files, prefix, ingest_config_path=ingest_path, s3=s3)
                summary["uploaded"] = True
                if media_id:
                    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    bump_previews_version(int(media_id), version)
                    summary["previews_version"] = version
            results.append(summary)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
