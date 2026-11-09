#!/usr/bin/env python3
"""Take tombstones off the site so browse and search don't list them.

    hide-tombstones.py --out DIR [--execute] [--item ID ...]

A tombstone is an item with dcterms:isReplacedBy (a duplicate merged into an older record; see
docs/tombstones.md). Omeka lists only items assigned to the site in browse, search and faceted
browse, but shows any public item's page, so a tombstone without a site keeps its ARK page.

Dry run by default: lists the tombstones still on a site. With --execute, per item: backup
(DIR/backups/item-N.before.json) -> full PUT with o:site emptied -> re-GET -> check that only
o:site changed. Undo: PUT the backup's o:site back. Run in omeka-catalog with
-e HITSAVE_CONFIG_ROOT=/config and the omeka-catalog.yaml mount.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

REPLACED_BY = "dcterms:isReplacedBy"
ALLOWED_CHANGES = {"o:site", "o:modified"}


def is_tombstone(item: dict) -> bool:
    return bool(item.get(REPLACED_BY))


def needs_change(item: dict) -> bool:
    return is_tombstone(item) and bool(item.get("o:site"))


def body_without_sites(item: dict) -> dict:
    body = {k: v for k, v in copy.deepcopy(item).items() if not k.startswith("@")}
    body["o:site"] = []
    return body


def unexpected_changes(before: dict, after: dict) -> list[str]:
    return sorted(k for k in set(before) | set(after) if k not in ALLOWED_CHANGES and before.get(k) != after.get(k))


def main() -> int:
    import requests

    from omeka_catalog_common import load_register_dip_omeka_api
    from yaml_config import load_yaml

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--item", type=int, action="append", default=[])
    args = ap.parse_args()

    config_root = Path(os.environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))
    api = load_register_dip_omeka_api()
    api_cfg = load_yaml(config_root / "omeka-catalog.yaml")["api"]
    cred_path = config_root / api_cfg["credentials_file"]
    if cred_path.parts and cred_path.parts[0] == "config":
        cred_path = config_root / Path(*cred_path.parts[1:])
    creds = load_yaml(cred_path)
    base = api_cfg["base_url"].rstrip("/")
    session = requests.Session()
    headers = {"Accept": "application/ld+json", "Content-Type": "application/json"}

    ids = sorted(set(args.item))
    if not ids:
        pid = api.resolve_property_id(session, base, creds, REPLACED_BY)
        page = 1
        while True:
            r = session.get(api.api_url(base, "items", creds), timeout=120, params={
                "property[0][property]": pid, "property[0][type]": "ex", "per_page": 100, "page": page})
            r.raise_for_status()
            batch = r.json()
            ids.extend(int(d["o:id"]) for d in batch)
            if len(batch) < 100:
                break
            page += 1

    (args.out / "backups").mkdir(parents=True, exist_ok=True)
    report, failed = [], 0
    for item_id in ids:
        url = api.api_url(base, f"items/{item_id}", creds)
        before = session.get(url, headers=headers, timeout=120)
        before.raise_for_status()
        before = before.json()
        if not needs_change(before):
            continue
        row = {"item": item_id, "title": before.get("o:title"), "public": before.get("o:is_public"),
               "sites": [s.get("o:id") for s in before.get("o:site") or []]}
        if args.execute:
            (args.out / "backups" / f"item-{item_id}.before.json").write_text(
                json.dumps(before, ensure_ascii=False, indent=1), encoding="utf-8")
            r = session.put(url, headers=headers, json=body_without_sites(before), timeout=120)
            if not r.ok:
                row["error"] = f"PUT {r.status_code}: {r.text[:300]}"
            else:
                after = session.get(url, headers=headers, timeout=120).json()
                row["other_changes"] = unexpected_changes(before, after)
                if after.get("o:site") or row["other_changes"]:
                    row["error"] = "verify failed"
            failed += 1 if row.get("error") else 0
        print(f"{item_id} {str(row['title'])[:60]!r} sites={row['sites']}"
              + (f" -> {row.get('error') or 'removed from site'}" if args.execute else ""), flush=True)
        report.append(row)
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(report)} tombstones {'taken off the site' if args.execute else 'on a site (dry run)'}; {failed} problems")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
