#!/usr/bin/env python3
"""Curl Moby game pages for ad blurbs; update ledger + Omeka for rows missing descriptions."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import psycopg
    import yaml
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
_default_config = Path("/config") if Path("/config/mobygames.yaml").is_file() else REPO_ROOT / "config"
CONFIG_ROOT = Path(os.environ.get("HITSAVE_CONFIG_ROOT", str(_default_config)))

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from moby_page_scrape import fetch_ad_blurb_from_web  # noqa: E402
from moby_resolve import finalize_moby_metadata, uses_moby_catalog_data  # noqa: E402


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def cookie_jar(moby_cfg: dict) -> Path | None:
    rel = moby_cfg.get("page_cookie_jar")
    if not rel:
        return None
    path = Path(str(rel))
    if path.parts and path.parts[0] == "config":
        path = CONFIG_ROOT / Path(*path.parts[1:])
    elif not path.is_absolute():
        path = CONFIG_ROOT / path
    return path if path.is_file() else None


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    moby_cfg = load_yaml(CONFIG_ROOT / "mobygames.yaml").get("mobygames") or {}
    min_chars = int(moby_cfg.get("ad_blurb_min_chars", 80))
    jar = cookie_jar(moby_cfg)

    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]

    conn = pg_connect(db_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, moby_game_id, metadata_json, moby_status
                FROM game_ingest
                WHERE moby_game_id IS NOT NULL
                ORDER BY game_key
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    updated: list[dict] = []
    for game_key, moby_game_id, metadata_json, moby_status in rows:
        if isinstance(metadata_json, str):
            metadata_json = json.loads(metadata_json)
        meta = dict(metadata_json or {})
        if not uses_moby_catalog_data(meta):
            meta["moby_game_id"] = moby_game_id
        has_desc = bool((meta.get("description_plain") or meta.get("description") or "").strip())
        has_ad = bool((meta.get("official_ad_blurb_plain") or "").strip())
        if has_desc or has_ad:
            continue
        moby_url = meta.get("moby_url") or f"https://www.mobygames.com/game/{moby_game_id}/"
        text, source = fetch_ad_blurb_from_web(moby_url, cookie_jar=jar, min_chars=min_chars)
        if len(text) < min_chars:
            updated.append(
                {
                    "game_key": game_key,
                    "fetched": False,
                    "moby_url": moby_url,
                    "cookie_jar": str(jar) if jar else None,
                }
            )
            continue
        meta["official_ad_blurb"] = text
        meta["official_ad_blurb_plain"] = text
        meta["official_ad_blurb_source"] = source or "MobyGames"
        meta["description_source"] = "ad_blurb"
        finalize_moby_metadata(meta)
        new_status = "ok" if moby_status in ("incomplete", None) else moby_status
        updated.append(
            {
                "game_key": game_key,
                "fetched": True,
                "source": source,
                "chars": len(text),
                "moby_status": new_status,
            }
        )
        if dry_run:
            continue
        conn = pg_connect(db_cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE game_ingest
                    SET metadata_json = %s, moby_status = %s, updated_at = NOW()
                    WHERE game_key = %s
                    """,
                    (json.dumps(meta), new_status, game_key),
                )
            conn.commit()
        finally:
            conn.close()

    print(json.dumps({"dry_run": dry_run, "results": updated}, indent=2))
    if not dry_run and any(u.get("fetched") for u in updated):
        subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/apply-moby-metadata-omeka.py"),
                "--all",
            ],
            check=False,
            env={**os.environ, "HITSAVE_CONFIG_ROOT": str(CONFIG_ROOT)},
        )


if __name__ == "__main__":
    main()
