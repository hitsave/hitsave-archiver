#!/usr/bin/env python3
"""Match Omeka items to ledger rows by title and set omeka_item_id / omeka_media_id."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import psycopg
    import requests
    import yaml
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from normalize_display_title import TITLE_SUFFIX_SEP, normalize_display_title  # noqa: E402


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def api_url(base: str, path: str, creds: dict) -> str:
    from urllib.parse import urlencode

    q = urlencode(
        {
            "key_identity": creds["key_identity"],
            "key_credential": creds["key_credential"],
        }
    )
    return f"{base.rstrip('/')}/{path.lstrip('/')}?{q}"


def title_match_key(title: str) -> str:
    text = normalize_display_title(title)
    if TITLE_SUFFIX_SEP in text:
        text = text.split(TITLE_SUFFIX_SEP, 1)[0].strip()
    text = re.sub(r"\s+", " ", text).casefold()
    return text


def fetch_omeka_items(session: requests.Session, base: str, creds: dict) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        url = api_url(base, "items", creds)
        resp = session.get(url, params={"page": page, "per_page": 100}, timeout=120)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out


def item_title(item: dict) -> str:
    rows = item.get("dcterms:title") or []
    for row in rows:
        val = (row.get("@value") or "").strip()
        if val:
            return val
    return ""


def primary_media_id(item: dict) -> int | None:
    media = item.get("o:media") or []
    if not media:
        return None
    return int(media[0]["o:id"])


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    api_cfg = load_yaml(CONFIG_ROOT / "omeka-uploader.yaml")["api"]
    cred_rel = Path(api_cfg["credentials_file"])
    if cred_rel.parts and cred_rel.parts[0] == "config":
        cred_rel = Path(*cred_rel.parts[1:])
    creds = load_yaml(CONFIG_ROOT / cred_rel)
    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]

    session = requests.Session()
    items = fetch_omeka_items(session, api_cfg["base_url"], creds)
    by_key: dict[str, list[dict]] = {}
    for item in items:
        title = item_title(item)
        if not title:
            continue
        by_key.setdefault(title_match_key(title), []).append(item)

    conn = pg_connect(db_cfg)
    updates: list[dict] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, omeka_item_id, metadata_json
                FROM game_ingest
                WHERE status = 'complete'
                ORDER BY game_key
                """
            )
            rows = cur.fetchall()
        for game_key, existing_item_id, metadata_json in rows:
            if existing_item_id:
                continue
            if isinstance(metadata_json, str):
                metadata_json = json.loads(metadata_json)
            meta = metadata_json or {}
            candidates: list[str | None] = []
            cfg_matches = list((CONFIG_ROOT / "preservation/generated").rglob(f"{game_key}.yaml"))
            if cfg_matches:
                gcfg = load_yaml(cfg_matches[0])
                candidates.append(gcfg.get("omeka_item_title"))
                candidates.append(Path(gcfg.get("source_game_folder", "")).name)
            candidates.extend(
                [
                    meta.get("search_title"),
                    game_key.replace("-", " "),
                    meta.get("moby_title"),
                ]
            )
            match_item = None
            for cand in candidates:
                if not cand:
                    continue
                key = title_match_key(str(cand))
                pool = by_key.get(key) or []
                if len(pool) == 1:
                    match_item = pool[0]
                    break
                if pool:
                    match_item = max(pool, key=lambda it: int(it["o:id"]))
                    break
            if not match_item:
                updates.append({"game_key": game_key, "matched": False})
                continue
            item_id = int(match_item["o:id"])
            media_id = primary_media_id(match_item)
            updates.append(
                {
                    "game_key": game_key,
                    "matched": True,
                    "omeka_item_id": item_id,
                    "omeka_media_id": media_id,
                    "title": item_title(match_item),
                }
            )
            if dry_run:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE game_ingest
                    SET omeka_item_id = %s, omeka_media_id = %s, updated_at = NOW()
                    WHERE game_key = %s
                    """,
                    (item_id, media_id, game_key),
                )
            conn.commit()
    finally:
        conn.close()

    matched = sum(1 for u in updates if u.get("matched"))
    print(json.dumps({"dry_run": dry_run, "matched": matched, "results": updates}, indent=2))


if __name__ == "__main__":
    main()
