#!/usr/bin/env python3
"""Disc contents of a data-disc dump: a second BagIt package with the disc's files and web copies.

The dump's own bag (the disc image) is never touched. Its contents bag lives in the same bucket
under disc-contents/ with the dump's path (dumps/<rest> -> disc-contents/<rest>), so it stays hot
when the image goes cold, and is attached to the Omeka item as its own media.

Steps (remote-intake sweep, remote_intake_disc_contents in common.sh):

  classify --slug S   ingest-worker: decide from the staged Redump sidecar (disc_contents_policy)
                      and record it on the ledger row. Exit 0: extract; exit 3: not shown.
  (disc-contents container: extract_disc_contents.py -> <output>/disc-contents/<slug>/)
  build --slug S      ingest-worker: ClamAV over the extracted files, build + validate the bag,
                      upload to Wasabi (verified), record prefix and summary on the ledger row.
  attach --slug S     omeka-catalog: create the Omeka media (public only when the decision is
                      public and the item is public) or refresh it (previews_version).

--game-key K works instead of --slug S (the ready/ folder comes from the ledger source_path).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import yaml  # noqa: E402

from disc_contents_policy import NONE, PUBLIC, REVIEW, DiscContentsDecision, classify_submission  # noqa: E402

EXIT_NOT_SHOWN = 3
CONTENTS_TOP = "disc-contents"
ACCESS_DIR = "metadata/hitsave/access"
AGENT = "HitSave build_disc_contents_bag.py"
ACCESS_MIME = {".mp4": "video/mp4", ".pdf": "application/pdf", ".jpg": "image/jpeg"}


def contents_prefix(dump_prefix: str) -> str:
    """dumps/<rest> -> disc-contents/<rest> (same bucket)."""
    p = dump_prefix.strip("/")
    if not p.startswith("dumps/"):
        raise ValueError(f"not a dump storage prefix: {dump_prefix!r}")
    return f"{CONTENTS_TOP}/{p[len('dumps/'):]}"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- ledger ---------------------------------------------------------------------------------

LEDGER_COLS = (
    "game_key, package_id, package_storage_prefix, source_path, omeka_item_id, status, "
    "disc_contents_display, disc_contents_media_id, disc_contents_prefix, disc_contents_class"
)


def ledger_row(conn, *, slug: str | None = None, game_key: str | None = None) -> dict | None:
    with conn.cursor() as cur:
        if game_key:
            cur.execute(f"SELECT {LEDGER_COLS} FROM game_ingest WHERE game_key = %s", (game_key,))
        else:
            cur.execute(
                f"SELECT {LEDGER_COLS} FROM game_ingest WHERE ingest_kind = 'dump' AND status = 'complete' "
                "AND (source_path LIKE %s OR source_path LIKE %s) ORDER BY id DESC LIMIT 1",
                (f"%/ready/{slug}", f"%/ready/{slug}/"),
            )
        row = cur.fetchone()
        if not row:
            return None
        names = [d[0] for d in cur.description]
    return dict(zip(names, row))


def update_ledger(conn, game_key: str, **cols) -> None:
    cols["disc_contents_updated_at"] = iso_now()
    sets = ", ".join(f"{k} = %s" for k in cols)
    values = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in cols.values()]
    with conn.cursor() as cur:
        cur.execute(f"UPDATE game_ingest SET {sets} WHERE game_key = %s", (*values, game_key))
    conn.commit()


# --- classify -------------------------------------------------------------------------------

def decide(ready: Path) -> DiscContentsDecision:
    from hitsave_dump_sidecars.redump import parse_submission_info
    from preservation_common import original_folder_name

    sidecar = ready / "!submissionInfo.txt"
    if not sidecar.is_file():
        return DiscContentsDecision("not_data_disc", "skip", ["no Redump sidecar"])
    fields = parse_submission_info(sidecar.read_text(encoding="utf-8-sig", errors="replace")).fields
    handoff = ready / "hitsave-submission.yaml"
    folder = None
    if handoff.is_file():
        folder = original_folder_name(yaml.safe_load(handoff.read_text(encoding="utf-8")) or {})
    return classify_submission(fields, folder_name=folder)


# --- bag ------------------------------------------------------------------------------------

def summarize(listing: dict) -> dict:
    return {
        "files": int(listing.get("total_files") or 0),
        "bytes": int(listing.get("total_bytes") or 0),
        "by_kind": listing.get("by_kind") or {},
        "volume_label": listing.get("volume_label"),
    }


def build_contents_bag(extract_dir: Path, bag_dir: Path, *, source: dict, decision: DiscContentsDecision,
                       scan: dict | None = None, preview_cfg: dict | None = None) -> dict:
    """Build and validate the contents bag from extract_disc_contents.py output; returns inventory."""
    import bagit

    from dip_package_previews import (
        MANIFEST_VERSION,
        _write_grid_thumb,
        index_payload_files,
        load_preview_config,
        write_hitsave_previews,
    )
    from yaml_config import dumps_json

    listing = json.loads((extract_dir / "listing.json").read_text(encoding="utf-8"))
    result = json.loads((extract_dir / "result.json").read_text(encoding="utf-8"))
    cfg = preview_cfg or load_preview_config()
    shutil.rmtree(bag_dir, ignore_errors=True)
    bag_dir.mkdir(parents=True)
    # Payload at the bag root (files/<volume>/…); make_bag moves it into data/.
    for child in (extract_dir / "files").iterdir():
        dest = bag_dir / child.name
        (shutil.copytree if child.is_dir() else shutil.copy2)(child, dest)
    bagit.make_bag(str(bag_dir), bag_info={
        "Bag-Software": AGENT,
        "External-Identifier": f"hitsave:{source['package_id']}:disc-contents",
        "Bag-Group-Identifier": source["package_id"],
        "Internal-Sender-Identifier": source["game_key"],
        "External-Description": (
            f"Files extracted from the disc image of {source['package_id']} "
            f"(s3://{source['bucket']}/{source['package_storage_prefix']}/), volume {listing.get('volume_label') or '?'}; "
            "the disc image bag is the preservation copy."
        ),
    }, checksums=["sha256"])

    keys = {f.relative_path: f.key for f in index_payload_files(bag_dir, "data")}
    access_root = bag_dir / ACCESS_DIR
    access_root.mkdir(parents=True, exist_ok=True)
    files, previews = [], {}
    for e in listing["files"]:
        rel = e["path"]
        key = keys[rel]
        rec = {"path": f"data/{rel}", "size_bytes": e["size"], "sha256": e["sha256"], "mime": e.get("mime"),
               "kind": e["kind"], "modified": e.get("mtime"), "download": e["kind"] != "program"}
        derived = e.get("derived") or {}
        if "access" in derived:
            src = extract_dir / derived["access"]
            suffix = src.suffix.lower()
            dest = access_root / f"{key}{suffix}"
            shutil.copy2(src, dest)
            rec["access"] = {"path": dest.relative_to(bag_dir).as_posix(),
                             "mime": ACCESS_MIME.get(suffix, "application/octet-stream"),
                             "size_bytes": dest.stat().st_size}
        previews[key] = (e["kind"], {k: extract_dir / v for k, v in derived.items() if k != "access"}, rec)
        files.append(rec)

    manifest = write_hitsave_previews(bag_dir, package_id=source["package_id"], config=cfg) or {
        "manifest_version": MANIFEST_VERSION, "package_id": source["package_id"], "browse": {},
        "grid": {}, "display": {}, "files": {}, "gallery_file_keys": [],
    }
    grid_edge, quality = int(cfg.get("grid_thumbnail_max_edge", 640)), int(cfg.get("grid_jpeg_quality", 88))
    meta = bag_dir / "metadata" / "hitsave"
    videos, documents = [], []
    for key, (kind, extra, rec) in sorted(previews.items(), key=lambda kv: int(kv[0][1:])):
        grid_src = display_src = None
        if kind == "video":
            grid_src, display_src = extra.get("poster"), extra.get("sheet")
            videos.append(key)
        elif kind == "document" and "page1" in extra:
            grid_src = extra["page1"]
            documents.append(key)
        elif kind == "image" and key not in manifest["grid"] and "access" in rec:
            grid_src = display_src = bag_dir / rec["access"]["path"]  # BMP/PCX/TGA/TIFF -> JPEG copy
            manifest.setdefault("gallery_file_keys", []).append(key)
        if grid_src is not None:
            grid_rel = f"metadata/hitsave/thumbnails/grid/{key}.jpg"
            (bag_dir / grid_rel).parent.mkdir(parents=True, exist_ok=True)
            if _write_grid_thumb(grid_src, bag_dir / grid_rel, grid_edge, quality):
                manifest["grid"][key] = grid_rel
        if display_src is not None:
            disp_rel = f"metadata/hitsave/display/{key}.jpg"
            (bag_dir / disp_rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(display_src, bag_dir / disp_rel)
            manifest.setdefault("display", {})[key] = disp_rel
        manifest.setdefault("files", {}).setdefault(key, {"path": rec["path"], "size": rec["size_bytes"],
                                                          "scan": False})
    manifest.update(disc_contents=True, video_file_keys=videos, document_file_keys=documents)
    (meta / "preview-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    inventory = {
        "inventory_version": 1,
        "kind": "disc_contents",
        "summary": {"file_count": len(files), "total_bytes": sum(f["size_bytes"] for f in files),
                    "by_kind": listing.get("by_kind") or {}},
        "source": {k: source[k] for k in ("game_key", "package_id", "package_storage_prefix", "bucket")},
        "disc": {k: listing.get(k) for k in ("volume_label", "image", "track_mode")},
        "files": files,
    }
    (bag_dir / "metadata" / "inventory.json").write_text(dumps_json(inventory, indent=2), encoding="utf-8")
    submission = {
        "kind": "disc_contents",
        "created_at": iso_now(),
        "source": inventory["source"],
        "classification": {"class": decision.disc_class, "display": decision.display, "reasons": decision.reasons},
        "extraction": {k: result.get(k) for k in ("generated_at", "image", "track_mode", "volume_label", "tools")},
        "virus_scan": scan,
    }
    (bag_dir / "metadata" / "submission.json").write_text(dumps_json(submission, indent=2), encoding="utf-8")
    bag = bagit.Bag(str(bag_dir))
    bag.save(manifests=True)
    bagit.Bag(str(bag_dir)).validate()
    return inventory


# --- commands -------------------------------------------------------------------------------

def _config():
    from yaml_config import load_yaml

    ingest_path = Path(os.environ.get("HITSAVE_INGEST_CONFIG", "/config/preservation/ingest.yaml"))
    ingest = load_yaml(ingest_path)
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    return ingest_path, ingest, db


def _ready_dir(row: dict, slug: str | None) -> Path:
    if slug:
        return Path(os.environ.get("HITSAVE_READY_ROOT", "/data/intake/ready")) / slug
    return Path(str(row["source_path"]).rstrip("/"))


def effective_display(decision: DiscContentsDecision, previous_class: str | None, previous_display: str | None) -> str:
    """The display to record. A public or none decision stands on a re-run while the class is the
    same, so staff decisions in the ledger (review -> public / none) aren't overwritten."""
    if previous_class == decision.disc_class and previous_display in (PUBLIC, NONE):
        return previous_display
    return decision.display


def cmd_classify(args) -> int:
    from upload_bag_wasabi import pg_connect

    _, _, db = _config()
    with pg_connect(db) as conn:
        row = ledger_row(conn, slug=args.slug, game_key=args.game_key)
        if row is None:
            print("disc contents: no complete dump in the ledger for this package; skipping")
            return EXIT_NOT_SHOWN
        decision = decide(_ready_dir(row, args.slug))
        display = effective_display(decision, row.get("disc_contents_class"), row.get("disc_contents_display"))
        if decision.display != "skip":  # console / video discs: leave their rows alone
            update_ledger(conn, row["game_key"], disc_contents_class=decision.disc_class,
                          disc_contents_display=display, disc_contents_reasons="; ".join(decision.reasons))
    print(json.dumps({"game_key": row["game_key"], "class": decision.disc_class, "display": display,
                      "reasons": decision.reasons}, ensure_ascii=False))
    return 0 if display in (PUBLIC, REVIEW) else EXIT_NOT_SHOWN


def cmd_build(args) -> int:
    import pyclamd

    from premis_virus_check import VirusDetectedError, scan_sip_tree
    from preservation_common import resolve_wasabi_bucket
    from upload_bag_wasabi import load_wasabi_settings, pg_connect, upload_bag_to_prefix

    ingest_path, ingest, db = _config()
    out_root = Path(ingest["paths"]["output_root"])
    with pg_connect(db) as conn:
        row = ledger_row(conn, slug=args.slug, game_key=args.game_key)
        if row is None or row.get("disc_contents_display") not in (PUBLIC, REVIEW):
            print(f"disc contents: nothing to build ({(row or {}).get('disc_contents_display')})")
            return EXIT_NOT_SHOWN
        slug = args.slug or Path(str(row["source_path"]).rstrip("/")).name
        extract_dir = out_root / "disc-contents" / slug
        result = json.loads((extract_dir / "result.json").read_text(encoding="utf-8"))
        if result.get("status") != "ok":
            update_ledger(conn, row["game_key"], disc_contents_reasons=(
                f"{row.get('disc_contents_display')}: extraction {result.get('status')} ({result.get('reason', '')})"))
            print(f"disc contents: extraction {result.get('status')}: {result.get('reason')}")
            return 1
        clam = ingest["clamav"]
        clamd = pyclamd.ClamdNetworkSocket(host=clam["host"], port=int(clam["port"]), timeout=300)
        try:
            report = scan_sip_tree(clamd, extract_dir / "files")
        except VirusDetectedError as exc:
            update_ledger(conn, row["game_key"], disc_contents_display=NONE,
                          disc_contents_reasons=f"ClamAV found malware in the disc's files: {exc}")
            print(f"disc contents: MALWARE, not published: {exc}")
            return 1
        scan = {k: v for k, v in asdict(report).items() if k != "file_scans"}
        wasabi, _ = load_wasabi_settings(ingest_path)
        bucket = resolve_wasabi_bucket(wasabi, "dump")
        source = {"game_key": row["game_key"], "package_id": row["package_id"] or row["game_key"],
                  "package_storage_prefix": str(row["package_storage_prefix"]).strip("/"), "bucket": bucket}
        decision = DiscContentsDecision(row.get("disc_contents_class") or "promotional", row["disc_contents_display"])
        bag_dir = out_root / "disc-contents-bag" / row["game_key"]
        inventory = build_contents_bag(extract_dir, bag_dir, source=source, decision=decision, scan=scan)
        prefix = contents_prefix(source["package_storage_prefix"])
        upload_bag_to_prefix(bag_dir, prefix, ingest_config_path=ingest_path, bucket=bucket, ingest_kind="dump")
        listing = json.loads((extract_dir / "listing.json").read_text(encoding="utf-8"))
        update_ledger(conn, row["game_key"], disc_contents_prefix=prefix, disc_contents_summary=summarize(listing))
    print(json.dumps({"game_key": row["game_key"], "prefix": prefix, "files": inventory["summary"]["file_count"],
                      "bytes": inventory["summary"]["total_bytes"]}))
    return 0


def cmd_attach(args) -> int:
    import requests

    from omeka_catalog_common import load_register_dip_omeka_api
    from omeka_external_dip import attach_external_dip_media
    from omeka_staff_metadata import load_omeka_catalog_config, require_external_dip_config, require_omeka_api_config
    from refresh_bag_previews_wasabi import bump_previews_version
    from upload_bag_wasabi import pg_connect
    from yaml_config import load_yaml

    ingest_path, _, db = _config()
    root = Path(os.environ.get("HITSAVE_CONFIG_ROOT", "/config"))
    catalog = load_omeka_catalog_config(root)
    api_cfg, dip_cfg = require_omeka_api_config(catalog), require_external_dip_config(catalog)
    cred = root / Path(*Path(api_cfg["credentials_file"]).parts[1:])
    if not cred.is_file():
        cred = root / "secrets" / Path(api_cfg["credentials_file"]).name
    creds = load_yaml(cred)
    api = load_register_dip_omeka_api()
    base = api_cfg["base_url"].rstrip("/")
    session = requests.Session()
    with pg_connect(db) as conn:
        row = ledger_row(conn, slug=args.slug, game_key=args.game_key)
        if row is None or not row.get("disc_contents_prefix") or row.get("disc_contents_display") not in (PUBLIC, REVIEW):
            print("disc contents: nothing to attach")
            return EXIT_NOT_SHOWN
        if not row.get("omeka_item_id"):
            print("disc contents: dump has no Omeka item yet")
            return 1
        item = session.get(api.api_url(base, f"items/{int(row['omeka_item_id'])}", creds), timeout=120)
        item.raise_for_status()
        is_public = row["disc_contents_display"] == PUBLIC and bool(item.json().get("o:is_public"))
        prefix = row["disc_contents_prefix"]
        version = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-disc-contents"
        media_id = row.get("disc_contents_media_id")
        if media_id:
            url = api.api_url(base, f"media/{int(media_id)}", creds)
            session.patch(url, json={"o:is_public": is_public}, timeout=120).raise_for_status()
        else:
            from preservation_common import resolve_wasabi_bucket
            from upload_bag_wasabi import load_wasabi_settings

            wasabi, _ = load_wasabi_settings(ingest_path)
            bucket = resolve_wasabi_bucket(wasabi, "dump")
            summary = row.get("disc_contents_summary") or {}
            media_id = attach_external_dip_media(
                session, base, creds, item_id=int(row["omeka_item_id"]), s3_uri=f"s3://{bucket}/{prefix}/",
                ingester=dip_cfg["dip_ingester"], is_public=is_public,
                package_bytes=int((summary or {}).get("bytes") or 0), package_storage_prefix=prefix,
                dip_storage=dip_cfg["dip_storage"], api_url=api.api_url,
            )
            update_ledger(conn, row["game_key"], disc_contents_media_id=media_id)
        bump_previews_version(int(media_id), version)
    print(json.dumps({"game_key": row["game_key"], "media_id": int(media_id), "public": is_public}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Disc contents bag for a data-disc dump")
    ap.add_argument("command", choices=["classify", "build", "attach"])
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--slug")
    who.add_argument("--game-key")
    args = ap.parse_args(argv)
    return {"classify": cmd_classify, "build": cmd_build, "attach": cmd_attach}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
