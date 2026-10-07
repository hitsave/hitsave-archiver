#!/usr/bin/env python3
"""Remove duplicate Omeka test items that share the same normalized title (keep richest row)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import requests
    import yaml
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import importlib.util  # noqa: E402


def _backfill_module():
    path = REPO_ROOT / "scripts" / "backfill-ledger-omeka-ids.py"
    spec = importlib.util.spec_from_file_location("backfill_ledger_omeka_ids", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_backfill = _backfill_module()
api_url = _backfill.api_url
fetch_omeka_items = _backfill.fetch_omeka_items
item_title = _backfill.item_title
load_yaml = _backfill.load_yaml
title_match_key = _backfill.title_match_key


def item_score(item: dict) -> tuple[int, int, int, int]:
    rights = item.get("dcterms:rights") or []
    desc = item.get("dcterms:description") or []
    media = item.get("o:media") or []
    moby_rights = any("mobygames" in (r.get("@value") or "").casefold() for r in rights)
    has_desc = any((r.get("@value") or "").strip() for r in desc)
    return (
        1 if moby_rights else 0,
        1 if has_desc else 0,
        len(media),
        int(item["o:id"]),
    )


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    api_cfg = load_yaml(CONFIG_ROOT / "omeka-uploader.yaml")["api"]
    cred_rel = Path(api_cfg["credentials_file"])
    if cred_rel.parts and cred_rel.parts[0] == "config":
        cred_rel = Path(*cred_rel.parts[1:])
    creds = load_yaml(CONFIG_ROOT / cred_rel)
    session = requests.Session()
    base = api_cfg["base_url"]
    items = fetch_omeka_items(session, base, creds)

    groups: dict[str, list[dict]] = {}
    for item in items:
        title = item_title(item)
        if not title:
            continue
        groups.setdefault(title_match_key(title), []).append(item)

    deleted: list[int] = []
    kept: list[dict] = []
    for key, group in sorted(groups.items()):
        if len(group) < 2:
            continue
        keeper = max(group, key=item_score)
        kept.append({"title_key": key, "omeka_item_id": keeper["o:id"], "title": item_title(keeper)})
        for item in group:
            if int(item["o:id"]) == int(keeper["o:id"]):
                continue
            item_id = int(item["o:id"])
            deleted.append(item_id)
            if dry_run:
                continue
            url = api_url(base, f"items/{item_id}", creds)
            resp = session.delete(url, timeout=120)
            if not resp.ok:
                raise RuntimeError(f"Delete item {item_id} failed ({resp.status_code}): {resp.text[:500]}")

    print(
        json.dumps(
            {
                "dry_run": dry_run,
                "duplicate_groups": len(kept),
                "deleted_item_ids": deleted,
                "kept": kept,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
