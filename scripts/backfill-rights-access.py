#!/usr/bin/env python3
"""One-time: add In Copyright (dcterms:rights) and the access level (dcterms:accessRights) to items.

    backfill-rights-access.py --out DIR [--execute] [--item ID ...] [--limit N]

Rights: every item without a RightsStatements.org value gets In Copyright (InC). A statement
already on the item (staff set InC-EDU by hand, for example) is kept, and so is the MobyGames
credit. Access, from the ledger row: press material "Public"; dumps "Not public, request access
if needed", or "Disc contents public. ..." when their disc contents are shown. Items with no
ledger row (made by hand in Omeka) get rights only; their access is left for staff. An existing
access value is never replaced.

Dry run by default: writes DIR/report.json and prints each change. With --execute, per item:
backup (DIR/backups/item-N.before.json) -> full PUT -> re-GET -> check that only rights and
access changed. Run in omeka-catalog with -e HITSAVE_CONFIG_ROOT=/config, the omeka-catalog.yaml
mount, and this script.
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

import rights_access as ra  # noqa: E402

IGNORED_ON_VERIFY = {"o:modified", ra.RIGHTS, ra.ACCESS}


def access_for(item: dict, ledger: dict | None) -> str | None:
    if not ledger:
        return None
    kind = str(ledger.get("ingest_kind") or "")
    return ra.access_label(kind, disc_contents_public=ledger.get("disc_contents_display") == "public")


def updated_body(item: dict, ledger: dict | None, pids: dict[str, int]) -> tuple[dict, list[str]]:
    """Full PUT body with rights/access set, and which of the two changed."""
    body = {k: v for k, v in copy.deepcopy(item).items() if not k.startswith("@")}
    changed = []
    rights = ra.merged_rights(item, None, pids[ra.RIGHTS])
    if rights != (item.get(ra.RIGHTS) or []):
        body[ra.RIGHTS] = rights
        changed.append(ra.RIGHTS)
    label = access_for(item, ledger)
    if label and not item.get(ra.ACCESS):
        body[ra.ACCESS] = ra.merged_access(item, label, pids[ra.ACCESS])
        changed.append(ra.ACCESS)
    return body, sorted(changed)


def unexpected_changes(before: dict, after: dict) -> list[str]:
    return sorted(k for k in set(before) | set(after)
                  if k not in IGNORED_ON_VERIFY and before.get(k) != after.get(k))


def main() -> int:
    import psycopg
    import requests

    from omeka_catalog_common import load_register_dip_omeka_api
    from yaml_config import load_yaml

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--item", type=int, action="append", default=[], help="only these items (repeatable)")
    ap.add_argument("--limit", type=int, default=0, help="stop after N changed items")
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
    pids = {term: api.resolve_property_id(session, base, creds, term) for term in (ra.RIGHTS, ra.ACCESS)}

    ingest = load_yaml(config_root / "preservation" / "ingest.yaml")
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    with psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                         user=db["user"], password=db["password"]) as conn:
        cur = conn.execute("SELECT omeka_item_id, ingest_kind, disc_contents_display FROM game_ingest "
                           "WHERE omeka_item_id IS NOT NULL")
        ledger = {int(r[0]): {"ingest_kind": r[1], "disc_contents_display": r[2]} for r in cur.fetchall()}

    ids = sorted(set(args.item))
    if not ids:
        page = 1
        while True:
            r = session.get(api.api_url(base, "items", creds), params={"per_page": 100, "page": page,
                                                                       "sort_by": "id", "sort_order": "asc"},
                            timeout=120)
            r.raise_for_status()
            batch = r.json()
            ids.extend(int(d["o:id"]) for d in batch)
            if len(batch) < 100:
                break
            page += 1

    (args.out / "backups").mkdir(parents=True, exist_ok=True)
    report, failed, counts = [], 0, {"items": len(ids), "unchanged": 0}
    for item_id in ids:
        url = api.api_url(base, f"items/{item_id}", creds)
        before = session.get(url, headers=headers, timeout=120)
        before.raise_for_status()
        before = before.json()
        body, changed = updated_body(before, ledger.get(item_id), pids)
        if not changed:
            counts["unchanged"] += 1
            continue
        row = {"item": item_id, "title": before.get("o:title"), "changed": changed,
               "access": (body.get(ra.ACCESS) or [{}])[0].get("@value"), "ledger": item_id in ledger}
        key = "+".join(changed) + (" (no ledger row)" if item_id not in ledger else "")
        counts[key] = counts.get(key, 0) + 1
        if args.execute:
            (args.out / "backups" / f"item-{item_id}.before.json").write_text(
                json.dumps(before, ensure_ascii=False, indent=1), encoding="utf-8")
            r = session.put(url, headers=headers, json=body, timeout=120)
            if not r.ok:
                row["error"] = f"PUT {r.status_code}: {r.text[:300]}"
                failed += 1
            else:
                after = session.get(url, headers=headers, timeout=120).json()
                row["other_changes"] = unexpected_changes(before, after)
                if row["other_changes"] or updated_body(after, ledger.get(item_id), pids)[1]:
                    row["error"] = "verify failed"
                    failed += 1
            print(f"{item_id} {changed} -> {row.get('error') or 'ok'}", flush=True)
        report.append(row)
        if args.limit and len(report) >= args.limit:
            break
    (args.out / "report.json").write_text(json.dumps({"counts": counts, "items": report}, ensure_ascii=False,
                                                     indent=1) + "\n", encoding="utf-8")
    print(json.dumps(counts, indent=1))
    print(f"{len(report)} items {'updated' if args.execute else 'to update (dry run)'}; {failed} problems")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
