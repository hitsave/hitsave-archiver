#!/usr/bin/env python3
"""Staff decision on a dump's disc contents (ledger "Disc contents" page).

    set-disc-contents-display.py --game-key K --display public|none [--user NAME]

public: the contents media becomes public when its item is public (a hidden item keeps it hidden
until the item is published). none: the media stays (or becomes) private. The decision is recorded
on the ledger row and stands when the sweep runs again (build_disc_contents_bag.py classify).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from omeka_catalog_common import load_register_dip_omeka_api, set_resource_public  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

CONFIG_ROOT = Path(os.environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))


def media_visibility(display: str, item_is_public: bool) -> bool:
    return display == "public" and item_is_public


def decision_note(display: str, user: str, now: str) -> str:
    verb = "published" if display == "public" else "kept private (no public contents)"
    return f"staff: {verb} by {user or 'staff'} at {now}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--game-key", required=True)
    ap.add_argument("--display", required=True, choices=["public", "none"])
    ap.add_argument("--user", default="")
    args = ap.parse_args()

    api = load_register_dip_omeka_api()
    api_cfg = load_yaml(CONFIG_ROOT / "omeka-catalog.yaml")["api"]
    cred_path = CONFIG_ROOT / api_cfg["credentials_file"]
    if cred_path.parts and cred_path.parts[0] == "config":
        cred_path = CONFIG_ROOT / Path(*cred_path.parts[1:])
    creds = load_yaml(cred_path)
    ingest = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    conn = api.pg_connect(db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT omeka_item_id, disc_contents_media_id, disc_contents_display, disc_contents_reasons "
                "FROM game_ingest WHERE game_key = %s",
                (args.game_key,),
            )
            row = cur.fetchone()
        if not row:
            raise SystemExit(f"No ledger row for {args.game_key!r}")
        item_id, media_id, current, reasons = row
        if current not in ("review", "public", "none"):
            raise SystemExit(f"{args.game_key}: disc contents aren't up for a decision (display={current!r})")
        session = requests.Session()
        base = api_cfg["base_url"].rstrip("/")
        public = False
        if media_id:
            item = session.get(api.api_url(base, f"items/{int(item_id)}", creds), timeout=120)
            item.raise_for_status()
            public = media_visibility(args.display, bool(item.json().get("o:is_public")))
            set_resource_public(session, base, creds, "media", int(media_id), is_public=public, api=api)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        note = decision_note(args.display, args.user, now)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE game_ingest SET disc_contents_display = %s, disc_contents_reasons = %s, "
                "disc_contents_updated_at = now() WHERE game_key = %s",
                (args.display, f"{reasons}; {note}" if reasons else note, args.game_key),
            )
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"game_key": args.game_key, "display": args.display, "media_id": media_id,
                      "media_public": public}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
