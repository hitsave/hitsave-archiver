#!/usr/bin/env python3
"""One-shot: move Redump dump bags on Wasabi under dumps/Redump/ (like dumps/No-Intro/).

    dumps/{System}/{Category}/{L}/{package_id}/  ->  dumps/Redump/{System}/{Category}/{L}/{package_id}/

Per package, each step resumable (re-running picks up where a previous run stopped):

1. Wasabi MOVE (server-side rename) of every object, verified: same keys, sizes and ETags at
   the destination, nothing left at the source. A failed move leaves the ledger untouched.
2. Ledger: package_storage_prefix and aip_wasabi_prefix point at the new prefix.
3. Bag tag files on Wasabi (bag-info.txt wasabi=, metadata/submission.json wasabi_key /
   package_storage_prefix, tagmanifest-sha256.txt) rewritten from the ledger, then verified:
   every tag file matches the Wasabi tagmanifest; manifest-sha256.txt (payload) is unchanged.
4. Omeka: the item's staff bibo:uri and the DIP media data (package_storage_prefix,
   dip_wasabi_uri, package_wasabi_uri) switched to the new prefix by exact string replacement;
   all other item values are kept (merged PATCH). Verified by re-reading.

Dry run unless --execute. Run in the omeka-catalog service (Wasabi, ledger and production Omeka
credentials). Hold the remote-intake lock so no sweep or prune runs meanwhile:

    docker compose run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" \\
      --entrypoint python omeka-catalog /app/scripts/relocate_redump_bags_to_redump_prefix.py \\
      [--game-key KEY ...] [--limit N] [--execute]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import psycopg  # noqa: E402
import requests  # noqa: E402

from omeka_catalog_common import load_register_dip_omeka_api  # noqa: E402
from preservation_common import wasabi_bucket_for_storage_prefix  # noqa: E402
from sync_dump_bag_metadata_from_ledger import sync_bag_from_ledger  # noqa: E402
from upload_bag_wasabi import load_wasabi_settings, make_s3_client  # noqa: E402
from wasabi_rename import WasabiMoveIncomplete, wasabi_move_prefix_verified  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

CONFIG_ROOT = Path("/config")
NEW_ROOT = "dumps/Redump/"


def redump_target_prefix(old_prefix: str) -> str:
    """dumps/{System}/... -> dumps/Redump/{System}/... (idempotent)."""
    p = old_prefix.strip("/")
    if p.startswith(NEW_ROOT) or p == NEW_ROOT.rstrip("/"):
        return p
    if not p.startswith("dumps/"):
        raise ValueError(f"not a dumps/ prefix: {old_prefix!r}")
    rest = p[len("dumps/"):]
    if rest.split("/", 1)[0] in ("No-Intro", "Others", "Nintendo Switch"):
        raise ValueError(f"not a Redump layout: {old_prefix!r}")
    return NEW_ROOT + rest


def s3_uri(bucket: str, prefix: str) -> str:
    return f"s3://{bucket}/{prefix.strip('/')}/"


def list_objects(s3, bucket: str, prefix: str) -> dict[str, tuple[int, str]]:
    """{relative key: (size, etag)} for objects under prefix/."""
    root = prefix.strip("/") + "/"
    out: dict[str, tuple[int, str]] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=root):
        for obj in page.get("Contents") or []:
            key = str(obj["Key"])
            if not key.endswith("/"):
                out[key[len(root):]] = (int(obj.get("Size") or 0), str(obj.get("ETag") or "").strip('"'))
    return out


def replace_in(obj, old: str, new: str):
    """Recursively replace old with new in every string (old is never a substring of new here)."""
    if isinstance(obj, str):
        return obj.replace(old, new)
    if isinstance(obj, list):
        return [replace_in(v, old, new) for v in obj]
    if isinstance(obj, dict):
        return {k: replace_in(v, old, new) for k, v in obj.items()}
    return obj


def contains(obj, needle: str) -> bool:
    if isinstance(obj, str):
        return needle in obj
    if isinstance(obj, list):
        return any(contains(v, needle) for v in obj)
    if isinstance(obj, dict):
        return any(contains(v, needle) for v in obj.values())
    return False


def tag_files_consistent(s3, bucket: str, prefix: str) -> list[str]:
    """Problems if any tag file listed in tagmanifest-sha256.txt doesn't match its digest."""
    problems = []
    tm = s3.get_object(Bucket=bucket, Key=f"{prefix}/tagmanifest-sha256.txt")["Body"].read().decode()
    for line in tm.splitlines():
        if not line.strip():
            continue
        digest, rel = line.split(None, 1)
        body = s3.get_object(Bucket=bucket, Key=f"{prefix}/{rel.strip()}")["Body"].read()
        if hashlib.sha256(body).hexdigest() != digest:
            problems.append(f"tag file digest mismatch: {rel.strip()}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="Move, update ledger, bag tag files and Omeka")
    ap.add_argument("--game-key", action="append", help="Only these packages (repeatable)")
    ap.add_argument("--limit", type=int, default=0, help="Stop after N packages (0 = all)")
    args = ap.parse_args()

    ingest_path = CONFIG_ROOT / "preservation" / "ingest.yaml"
    ingest_cfg = load_yaml(ingest_path)
    wasabi_cfg, _ = load_wasabi_settings(ingest_path)
    creds_w = load_yaml(CONFIG_ROOT / str(wasabi_cfg["credentials_file"]).removeprefix("config/"))
    s3 = make_s3_client(wasabi_cfg, creds_w)
    db = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]

    def connect():
        return psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                               user=db["user"], password=db["password"])

    api = load_register_dip_omeka_api()
    ocfg = load_yaml(CONFIG_ROOT / "omeka-catalog.yaml")["api"]
    ocreds = load_yaml(CONFIG_ROOT / str(ocfg["credentials_file"]).removeprefix("config/"))
    obase = ocfg["base_url"]
    http = requests.Session()
    hdr = {"Accept": "application/ld+json"}

    where = ["ingest_kind = 'dump'", "dump_class = 'redump'", "status = 'complete'",
             "COALESCE(package_storage_prefix, '') <> ''"]
    params: list = []
    if args.game_key:
        where.append("game_key = ANY(%s)")
        params.append(args.game_key)
    with connect() as conn:
        rows = conn.execute(
            "SELECT game_key, title_slug, package_storage_prefix, aip_wasabi_prefix, sort_letter, "
            f"omeka_item_id, omeka_media_id FROM game_ingest WHERE {' AND '.join(where)} ORDER BY game_key",
            params,
        ).fetchall()

    todo = []
    for game_key, title_slug, prefix, aip, letter, item_id, media_id in rows:
        old = str(prefix).strip("/")
        old_root = old if not old.startswith(NEW_ROOT) else "dumps/" + old[len(NEW_ROOT):]
        new = redump_target_prefix(old)
        bucket = wasabi_bucket_for_storage_prefix(wasabi_cfg, old, ingest_kind="dump")
        todo.append(dict(game_key=game_key, title_slug=title_slug, old=old_root, new=new, bucket=bucket,
                         ledger_prefix=old, item_id=item_id, media_id=media_id, sort_letter=letter))
    if args.limit:
        todo = todo[: args.limit]

    pending = [t for t in todo if t["ledger_prefix"] != t["new"]]
    print(json.dumps({"dry_run": not args.execute, "redump_packages": len(rows),
                      "selected": len(todo), "ledger_not_yet_moved": len(pending)}))
    if not args.execute:
        for t in pending[:10]:
            print(f"  {t['game_key']}: s3://{t['bucket']}/{t['old']}/ -> {t['new']}/")
        return

    failures: list[str] = []
    for n, t in enumerate(todo, 1):
        key, bucket, old, new = t["game_key"], t["bucket"], t["old"], t["new"]
        old_uri, new_uri = s3_uri(bucket, old), s3_uri(bucket, new)
        log = []
        try:
            # 1. Wasabi move (skip when already done; refuse ambiguous states).
            at_old = list_objects(s3, bucket, old)
            at_new = list_objects(s3, bucket, new)
            if at_old and at_new:
                raise RuntimeError(f"objects at both prefixes ({len(at_old)} old, {len(at_new)} new); reconcile by hand")
            if at_old:
                wasabi_move_prefix_verified(s3, bucket=bucket, source_prefix=old, destination_prefix=new)
                moved = list_objects(s3, bucket, new)
                if moved != at_old:
                    bad = sorted(k for k in at_old if moved.get(k) != at_old[k])[:5]
                    raise RuntimeError(f"size/ETag differs after move for {bad}")
                log.append(f"moved {len(at_old)} objects")
            elif not at_new:
                raise RuntimeError("no objects at old or new prefix")
            else:
                log.append("already moved")

            # 2. Ledger.
            if t["ledger_prefix"] != new:
                with connect() as conn:
                    conn.execute(
                        "UPDATE game_ingest SET package_storage_prefix = %s, aip_wasabi_prefix = %s, "
                        "updated_at = NOW() WHERE game_key = %s AND package_storage_prefix = %s",
                        (new, new_uri, key, t["ledger_prefix"]),
                    )
                log.append("ledger updated")

            # 3. Bag tag files.
            payload_manifest_before = list_objects(s3, bucket, new).get("manifest-sha256.txt")
            bag_info = s3.get_object(Bucket=bucket, Key=f"{new}/bag-info.txt")["Body"].read().decode()
            if f"wasabi={new_uri}" not in bag_info:
                ledger_row = {"game_key": key, "title_slug": t["title_slug"], "package_storage_prefix": new,
                              "aip_wasabi_prefix": new_uri, "sort_letter": t["sort_letter"]}
                sync_bag_from_ledger(s3, bucket=bucket, storage_prefix=new, ledger_row=ledger_row, execute=True)
                log.append("tag files rewritten")
            problems = tag_files_consistent(s3, bucket, new)
            sub = json.loads(s3.get_object(Bucket=bucket, Key=f"{new}/metadata/submission.json")["Body"].read())
            if sub.get("package_storage_prefix") != new:
                problems.append(f"submission.json package_storage_prefix = {sub.get('package_storage_prefix')!r}")
            if list_objects(s3, bucket, new).get("manifest-sha256.txt") != payload_manifest_before:
                problems.append("payload manifest changed")
            if problems:
                raise RuntimeError("; ".join(problems))

            # 4. Omeka item staff URI + DIP media data.
            if t["item_id"]:
                item = http.get(api.api_url(obase, f"items/{t['item_id']}", ocreds), headers=hdr, timeout=120).json()
                if contains(item.get("bibo:uri"), old_uri):
                    api.patch_item_rdf_values(http, obase, ocreds, int(t["item_id"]),
                                              {"bibo:uri": replace_in(item["bibo:uri"], old_uri, new_uri)},
                                              item_body=item)
                    log.append("omeka item uri")
                item = http.get(api.api_url(obase, f"items/{t['item_id']}", ocreds), headers=hdr, timeout=120).json()
                if contains(item, old_uri):
                    raise RuntimeError("Omeka item still contains the old URI")
            if t["media_id"]:
                murl = api.api_url(obase, f"media/{t['media_id']}", ocreds)
                media = http.get(murl, headers=hdr, timeout=120).json()
                data = media.get("data") if isinstance(media.get("data"), dict) else {}
                if contains(data, old):
                    r = http.patch(murl, headers={**hdr, "Content-Type": "application/json"},
                                   json={"data": replace_in(data, old, new)}, timeout=120)
                    if not r.ok:
                        raise RuntimeError(f"PATCH media failed ({r.status_code}): {r.text[:300]}")
                    log.append("omeka media data")
                data = http.get(murl, headers=hdr, timeout=120).json().get("data") or {}
                if data.get("package_storage_prefix") != new or contains(data, old + "/"):
                    raise RuntimeError(f"Omeka media data not updated: {data.get('package_storage_prefix')!r}")
            print(f"[{n}/{len(todo)}] OK   {key}: {', '.join(log) or 'nothing to do'}", flush=True)
        except (WasabiMoveIncomplete, RuntimeError, ValueError) as e:
            failures.append(key)
            print(f"[{n}/{len(todo)}] FAIL {key}: {e} (done so far: {', '.join(log) or 'nothing'})", flush=True)

    print(json.dumps({"selected": len(todo), "failed": len(failures), "failed_keys": failures[:20]}))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
