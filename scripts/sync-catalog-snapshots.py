#!/usr/bin/env python3
"""Keep a dated copy of each package's catalog record inside its bag (metadata/catalog/).

For every complete ledger package with an Omeka item, builds a snapshot of the item
(catalog_snapshot.py) and compares it with the newest copy in the bag on Wasabi. When the bag
has no copy yet, or the record has changed, it adds a new dated file, a PREMIS event and their
tagmanifest lines. Older copies are never changed. Unchanged records cost two small reads.

    sync-catalog-snapshots.py [--execute] [--game-key KEY ...] [--max-writes N]

Dry run by default: prints what would be added. Run in omeka-catalog (it has the Omeka API
config and the Wasabi and ledger settings under /config).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import catalog_snapshot as cs  # noqa: E402

PER_PAGE = 100


class NotFound(Exception):
    pass


def _read(s3, bucket: str, key: str) -> bytes | None:
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except s3.exceptions.NoSuchKey:
        return None


def _manifest_digest(tagmanifest: str, relpath: str) -> str | None:
    for line in tagmanifest.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip() == relpath:
            return parts[0]
    return None


def _put_verified(s3, bucket: str, key: str, body: bytes, content_type: str) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
    stored = _read(s3, bucket, key)
    if stored is None or hashlib.sha256(stored).digest() != hashlib.sha256(body).digest():
        raise RuntimeError(f"s3://{bucket}/{key} does not match what was uploaded")


def sync_bag(s3, *, bucket: str, root: str, snapshot: dict, execute: bool) -> dict:
    """Add *snapshot* to the bag at s3://bucket/root unless its newest copy says the same."""
    tm_key = f"{root}tagmanifest-sha256.txt"
    tm_raw = _read(s3, bucket, tm_key)
    if not tm_raw or not tm_raw.strip():
        raise RuntimeError(f"no tagmanifest at s3://{bucket}/{tm_key}")
    tagmanifest = tm_raw.decode("utf-8")

    previous = None
    prev_rel = cs.latest_snapshot_relpath(tagmanifest)
    if prev_rel:
        body = _read(s3, bucket, root + prev_rel)
        if body is None or hashlib.sha256(body).hexdigest() != _manifest_digest(tagmanifest, prev_rel):
            raise RuntimeError(f"s3://{bucket}/{root}{prev_rel} does not match its tagmanifest line")
        previous = json.loads(body)
        if cs.content_digest(previous) == cs.content_digest(snapshot):
            return {"status": "unchanged", "latest": prev_rel}

    files, new_tm = cs.stage_snapshot(tagmanifest, snapshot, previous=previous, previous_relpath=prev_rel)
    result = {"status": "changed" if previous else "new", "add": sorted(files)}
    if previous:
        result["previous"] = prev_rel
        result["changed"] = cs.changed_fields(previous, snapshot)
    if not execute:
        result["dry_run"] = True
        return result

    for rel, body in files.items():
        kind = "application/json" if rel.endswith(".json") else "application/xml"
        _put_verified(s3, bucket, root + rel, body, kind)
    if _read(s3, bucket, tm_key) != tm_raw:
        raise RuntimeError(f"tagmanifest changed during the update of s3://{bucket}/{root}; run again")
    _put_verified(s3, bucket, tm_key, new_tm.encode("utf-8"), "text/plain")
    result["dry_run"] = False
    return result


class Names:
    """Item set titles, template labels and class terms, one API read per id."""

    def __init__(self, fetch: Callable[[str], dict]):
        self._fetch = fetch
        self._cache: dict[str, dict] = {"item_sets": {}, "templates": {}, "classes": {}}

    def _get(self, kind: str, path: str, field: str, rid: int):
        cache = self._cache[kind]
        if rid not in cache:
            try:
                cache[rid] = self._fetch(f"{path}/{rid}").get(field)
            except NotFound:
                cache[rid] = None
        return cache[rid]

    def for_items(self, items: Iterable[dict]) -> dict:
        out: dict[str, dict] = {"item_sets": {}, "templates": {}, "classes": {}}
        for it in items:
            for ref in it.get("o:item_set") or []:
                out["item_sets"][int(ref["o:id"])] = self._get("item_sets", "item_sets", "o:title", int(ref["o:id"]))
            if (ref := it.get("o:resource_template")) and ref.get("o:id") is not None:
                out["templates"][int(ref["o:id"])] = self._get(
                    "templates", "resource_templates", "o:label", int(ref["o:id"]))
            if (ref := it.get("o:resource_class")) and ref.get("o:id") is not None:
                out["classes"][int(ref["o:id"])] = self._get(
                    "classes", "resource_classes", "o:term", int(ref["o:id"]))
        return out


def ledger_items(fetch_page: Callable[[int], list[dict]], wanted: set[int]) -> dict[int, dict]:
    """All Omeka items whose id is in *wanted*, paging through the item list (100 per read)."""
    found: dict[int, dict] = {}
    page = 1
    while True:
        rows = fetch_page(page)
        if not rows:
            return found
        for it in rows:
            if int(it["o:id"]) in wanted:
                found[int(it["o:id"])] = it
        page += 1


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> None:
    import psycopg
    import requests

    from omeka_catalog_common import load_register_dip_omeka_api
    from preservation_common import wasabi_bucket_for_storage_prefix, wasabi_unpacked_prefix
    from upload_bag_wasabi import load_wasabi_settings, make_s3_client, resolve_config_path
    from yaml_config import load_yaml

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true", help="write to Wasabi (default: dry run)")
    parser.add_argument("--game-key", action="append", default=[], help="only these packages (repeatable)")
    parser.add_argument("--max-writes", type=int, default=0, help="stop after this many bags are updated (0: no limit)")
    args = parser.parse_args()

    config_root = Path(os.environ.get("HITSAVE_CONFIG_ROOT", "/config"))
    api = load_register_dip_omeka_api()
    api_cfg = load_yaml(config_root / "omeka-catalog.yaml")["api"]
    cred_rel = Path(api_cfg["credentials_file"])
    if cred_rel.parts and cred_rel.parts[0] == "config":
        cred_rel = Path(*cred_rel.parts[1:])
    omeka_creds = load_yaml(config_root / cred_rel)
    base = api_cfg["base_url"].rstrip("/")
    ingest_path = config_root / "preservation" / "ingest.yaml"
    ingest = load_yaml(ingest_path)
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    wasabi, _ = load_wasabi_settings(ingest_path)
    s3 = make_s3_client(wasabi, load_yaml(resolve_config_path(str(wasabi["credentials_file"]), config_root)))

    sql = ("SELECT game_key, ingest_kind, package_storage_prefix, omeka_item_id FROM game_ingest "
           "WHERE status = 'complete' AND omeka_item_id IS NOT NULL AND package_storage_prefix IS NOT NULL")
    params: tuple = ()
    if args.game_key:
        sql += " AND game_key = ANY(%s)"
        params = (args.game_key,)
    with psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                         user=db["user"], password=db["password"]) as conn:
        rows = conn.execute(sql + " ORDER BY game_key", params).fetchall()

    session = requests.Session()
    headers = {"Accept": "application/ld+json"}

    def fetch(path: str) -> dict:
        r = session.get(api.api_url(base, path, omeka_creds), headers=headers, timeout=120)
        if r.status_code == 404:
            raise NotFound(path)
        r.raise_for_status()
        return r.json()

    def fetch_page(page: int) -> list[dict]:
        r = session.get(api.api_url(base, "items", omeka_creds), headers=headers, timeout=300,
                        params={"page": page, "per_page": PER_PAGE, "sort_by": "id", "sort_order": "asc"})
        r.raise_for_status()
        return r.json()

    wanted = {int(r[3]) for r in rows}
    if len(wanted) <= 20:
        items = {}
        for iid in sorted(wanted):
            try:
                items[iid] = fetch(f"items/{iid}")
            except NotFound:
                pass
    else:
        items = ledger_items(fetch_page, wanted)
    names = Names(fetch).for_items(items.values())

    counts: dict[str, int] = {}
    report = []
    writes = 0
    for game_key, kind, prefix, item_id in rows:
        it = items.get(int(item_id))
        if it is None:
            status = {"status": "missing_in_omeka"}
        elif args.max_writes and writes >= args.max_writes:
            status = {"status": "skipped_max_writes"}
        else:
            snapshot = cs.build_snapshot(it, package_id=game_key, taken_at=iso_now(), names=names)
            bucket = wasabi_bucket_for_storage_prefix(wasabi, prefix, ingest_kind=kind)
            try:
                status = sync_bag(s3, bucket=bucket, root=wasabi_unpacked_prefix(prefix),
                                  snapshot=snapshot, execute=args.execute)
            except Exception as exc:  # one bad bag must not stop the others
                status = {"status": "error", "error": str(exc)[:300]}
            if status["status"] in ("new", "changed"):
                writes += 1
        counts[status["status"]] = counts.get(status["status"], 0) + 1
        if status["status"] != "unchanged":
            report.append({"game_key": game_key, "omeka_item_id": int(item_id), **status})
    print(json.dumps({"execute": args.execute, "packages": len(rows), "counts": counts, "packages_detail": report},
                     indent=2, ensure_ascii=False))
    if counts.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
