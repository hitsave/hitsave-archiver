#!/usr/bin/env python3
"""One-time: move "Donated by X." credit lines from Omeka descriptions into dcterms:provenance.

    backfill-donated-by-provenance.py --out DIR [--execute] [--item ID ...]

Staff wrote donor credits as a line of their own at the end of the description ("Donated by
Anthony Perkins." / "Item donated by …"). The templates now have Provenance, labelled "Donated
by". Per item: the credit line leaves the description (blank lines closed up), the name becomes a
provenance value (not duplicated if it's already there), every other value stays.

Dry run by default: writes DIR/report.json and prints each change. With --execute, per item:
backup (DIR/backups/item-N.before.json) -> full PUT -> re-GET -> check only description and
provenance changed. Only lines that are wholly a credit are moved; "donated" in prose is not.
Run in omeka-catalog: -e HITSAVE_CONFIG_ROOT=/config, the omeka-catalog.yaml mount, this script.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

PROVENANCE_ID = 51  # dcterms:provenance
DESCRIPTION = "dcterms:description"
PROVENANCE = "dcterms:provenance"
SEARCH = "donated by"
_CREDIT = re.compile(r"^\s*(?:item\s+)?donated\s+by\s*:?\s+(?P<name>\S.*?)\s*$", re.IGNORECASE)
_ABBREV = re.compile(r"\b(?:inc|ltd|co|corp|jr|sr|bros|st)\.$", re.IGNORECASE)


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if name.endswith(".") and not _ABBREV.search(name):
        name = name[:-1].rstrip()
    return name


def split_donor_lines(text: str) -> tuple[str, list[str]]:
    """(text without its credit lines, donor names in order). Blank-line runs left behind by a
    removed line are closed up; nothing else in the text changes."""
    donors: list[str] = []
    kept: list[str] = []
    for line in text.split("\n"):
        m = _CREDIT.match(line)
        if m and _clean_name(m["name"]):
            donors.append(_clean_name(m["name"]))
            continue
        kept.append(line)
    if not donors:
        return text, []
    out = "\n".join(kept)
    out = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", out)
    return out.strip(), donors


def updated_body(item: dict) -> tuple[dict, list[str]]:
    """Full PUT body for the item with credit lines moved to provenance, and the donors found."""
    body = {k: v for k, v in copy.deepcopy(item).items() if not k.startswith("@")}
    donors: list[str] = []
    new_desc = []
    for value in body.get(DESCRIPTION) or []:
        if value.get("type") == "literal" and value.get("@value"):
            text, found = split_donor_lines(value["@value"])
            donors.extend(found)
            if not text:
                continue
            value = {**value, "@value": text}
        new_desc.append(value)
    if not donors:
        return body, []
    if new_desc:
        body[DESCRIPTION] = new_desc
    else:
        body.pop(DESCRIPTION, None)
    prov = list(body.get(PROVENANCE) or [])
    have = {str(v.get("@value") or "").casefold() for v in prov}
    for name in donors:
        if name.casefold() not in have:
            prov.append({"type": "literal", "property_id": PROVENANCE_ID, "@value": name, "is_public": True})
            have.add(name.casefold())
    body[PROVENANCE] = prov
    return body, donors


def _changed_keys(before: dict, after: dict) -> list[str]:
    skip = {"o:modified", DESCRIPTION, PROVENANCE}
    return sorted(k for k in set(before) | set(after) if k not in skip and before.get(k) != after.get(k))


def main() -> int:
    import requests

    from omeka_catalog_common import load_register_dip_omeka_api
    from yaml_config import load_yaml

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--item", type=int, action="append", default=[], help="only these items (repeatable)")
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

    ids = set(args.item)
    if not ids:
        page = 1
        while True:
            r = session.get(api.api_url(base, "items", creds), timeout=120, params={
                "property[0][property]": 4, "property[0][type]": "in", "property[0][text]": SEARCH,
                "per_page": 100, "page": page})
            r.raise_for_status()
            batch = r.json()
            ids.update(int(d["o:id"]) for d in batch)
            if len(batch) < 100:
                break
            page += 1

    (args.out / "backups").mkdir(parents=True, exist_ok=True)
    report, failed = [], 0
    for item_id in sorted(ids):
        url = api.api_url(base, f"items/{item_id}", creds)
        before = session.get(url, headers=headers, timeout=120)
        before.raise_for_status()
        before = before.json()
        body, donors = updated_body(before)
        row = {"item": item_id, "title": before.get("o:title"), "public": before.get("o:is_public"), "donors": donors}
        if not donors:
            continue
        print(f"{item_id} {str(before.get('o:title'))[:60]!r}: donated by {', '.join(donors)}")
        if args.execute:
            (args.out / "backups" / f"item-{item_id}.before.json").write_text(
                json.dumps(before, ensure_ascii=False, indent=1), encoding="utf-8")
            r = session.put(url, headers=headers, json=body, timeout=120)
            if not r.ok:
                row["error"] = f"PUT {r.status_code}: {r.text[:300]}"
                failed += 1
            else:
                after = session.get(url, headers=headers, timeout=120).json()
                row["other_changes"] = _changed_keys(before, after)
                row["provenance_now"] = [v.get("@value") for v in after.get(PROVENANCE) or []]
                row["credit_lines_left"] = updated_body(after)[1]
                if row["other_changes"] or row["credit_lines_left"]:
                    failed += 1
            print(f"  -> {row.get('error') or ('provenance ' + str(row['provenance_now']) + ', other changes ' + str(row['other_changes']))}")
        report.append(row)
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(report)} items {'updated' if args.execute else 'to update (dry run)'}; {failed} problems")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
