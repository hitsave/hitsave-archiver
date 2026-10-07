#!/usr/bin/env python3
"""Update existing Omeka item dcterms:title values using normalize_display_title."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

try:
    import requests
    import yaml
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path(os.environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from normalize_display_title import normalize_display_title  # noqa: E402


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def api_url(base: str, path: str, creds: dict) -> str:
    q = urlencode(
        {"key_identity": creds["key_identity"], "key_credential": creds["key_credential"]}
    )
    return f"{base.rstrip('/')}/{path.lstrip('/')}?{q}"


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    api_cfg = load_yaml(CONFIG_ROOT / "omeka-test/omeka-api.yaml")["api"]
    cred_rel = Path(api_cfg["credentials_file"])
    if cred_rel.parts and cred_rel.parts[0] == "config":
        cred_rel = Path(*cred_rel.parts[1:])
    creds_path = CONFIG_ROOT / cred_rel
    creds = load_yaml(creds_path)
    base = api_cfg["base_url"]
    session = requests.Session()

    props = session.get(
        api_url(base, "properties", creds),
        params={"term": "dcterms:title"},
        headers={"Accept": "application/ld+json"},
        timeout=120,
    ).json()
    title_pid = int(props[0]["o:id"])

    items = session.get(
        api_url(base, "items", creds),
        params={"limit": 500, "sort_by": "id", "sort_order": "asc"},
        headers={"Accept": "application/ld+json"},
        timeout=120,
    ).json()

    updated = []
    for item in items:
        item_id = int(item["o:id"])
        rows = item.get("dcterms:title") or []
        if not rows:
            continue
        old = (rows[0].get("@value") or "").strip()
        new = normalize_display_title(old)
        if new == old:
            continue
        payload = {
            "dcterms:title": [
                {
                    "property_id": title_pid,
                    "type": "literal",
                    "@value": new,
                }
            ],
        }
        if dry_run:
            updated.append({"item_id": item_id, "old": old, "new": new})
            continue
        resp = session.patch(
            api_url(base, f"items/{item_id}", creds),
            headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=120,
        )
        if not resp.ok:
            print(f"Item {item_id} failed: {resp.text[:500]}", file=sys.stderr)
            continue
        updated.append({"item_id": item_id, "old": old, "new": new})

    print(json.dumps({"dry_run": dry_run, "updated": updated, "count": len(updated)}, indent=2))


if __name__ == "__main__":
    main()
