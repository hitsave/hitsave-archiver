#!/usr/bin/env python3
"""Upload a preservation DIP .tar to Omeka S via the REST API (multipart)."""
from __future__ import annotations

import json
import mimetypes
import sys
from pathlib import Path
from urllib.parse import urlencode

try:
    import psycopg
    import requests
    import yaml
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

import os

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path(os.environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from moby_omeka_fields import append_moby_fields_to_payload  # noqa: E402
from moby_resolve import uses_moby_catalog_data  # noqa: E402
from normalize_display_title import normalize_display_title  # noqa: E402
from preservation_common import resolve_game_config  # noqa: E402


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def api_url(base: str, path: str, creds: dict) -> str:
    q = urlencode(
        {
            "key_identity": creds["key_identity"],
            "key_credential": creds["key_credential"],
        }
    )
    return f"{base.rstrip('/')}/{path.lstrip('/')}?{q}"


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def resolve_site_id(session: requests.Session, base: str, creds: dict, slug: str) -> int:
    url = api_url(base, "sites", creds)
    resp = session.get(url, params={"slug": slug}, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise RuntimeError(f"No site with slug {slug!r}")
    return int(data[0]["o:id"])


def resolve_property_id(session: requests.Session, base: str, creds: dict, term: str) -> int:
    url = api_url(base, "properties", creds)
    resp = session.get(url, params={"term": term}, timeout=120)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise RuntimeError(f"{term} property not found")
    return int(rows[0]["o:id"])


def resolve_item_set_id(session: requests.Session, base: str, creds: dict, title: str) -> int:
    url = api_url(base, "item_sets", creds)
    resp = session.get(url, params={"title": title}, timeout=120)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise RuntimeError(f"Item set not found: {title!r}; run ensure-press-item-set.php")
    return int(rows[0]["o:id"])


def literal_value(property_id: int, value: str) -> dict:
    return {"property_id": property_id, "type": "literal", "@value": value}


def uri_value(property_id: int, value: str) -> dict:
    return {"property_id": property_id, "type": "uri", "@id": value}


def item_has_dip_media(session: requests.Session, base: str, creds: dict, item_id: int) -> bool:
    url = api_url(base, f"items/{item_id}", creds)
    resp = session.get(url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        return False
    body = resp.json()
    title_rows = body.get("dcterms:title") or []
    has_title = any((row.get("@value") or "").strip() for row in title_rows)
    media = body.get("o:media") or []
    return has_title and len(media) > 0


def create_item_with_dip(
    session: requests.Session,
    base: str,
    creds: dict,
    *,
    title: str,
    site_id: int,
    item_set_id: int,
    is_public: bool,
    title_property_id: int,
    tar_path: Path,
    ingester: str,
    extra_values: dict[str, list] | None = None,
) -> tuple[int, int]:
    """One multipart POST /api/items with file[0] (see Omeka S REST API)."""
    payload: dict = {
        "dcterms:title": [literal_value(title_property_id, title)],
        "o:is_public": is_public,
        "o:site": [{"o:id": site_id}],
        "o:item_set": [{"o:id": item_set_id}],
        "o:media": [
            {
                "o:ingester": ingester,
                "file_index": 0,
                "o:is_public": is_public,
            }
        ],
    }
    if extra_values:
        payload.update(extra_values)
    mime = mimetypes.guess_type(tar_path.name)[0] or "application/x-tar"
    url = api_url(base, "items", creds)
    with tar_path.open("rb") as handle:
        resp = session.post(
            url,
            headers={"Accept": "application/ld+json"},
            data={"data": json.dumps(payload)},
            files={"file[0]": (tar_path.name, handle, mime)},
            timeout=3600,
        )
    if not resp.ok:
        raise RuntimeError(f"Item+DIP upload failed ({resp.status_code}): {resp.text[:2000]}")

    body = resp.json()
    item_id = int(body["o:id"])
    title_rows = body.get("dcterms:title") or []
    if not any((row.get("@value") or "").strip() for row in title_rows):
        raise RuntimeError(f"Item {item_id} created without dcterms:title in API response")
    media_refs = body.get("o:media") or []
    if not media_refs:
        raise RuntimeError(f"Item {item_id} created without o:media in API response")
    media_id = int(media_refs[0]["o:id"])
    return item_id, media_id


def update_ledger(conn, game_key: str, item_id: int, media_id: int) -> None:
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


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: upload-dip-omeka-api.py <game_key> [path/to/game.yaml]")

    game_key = sys.argv[1]
    game_cfg_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if game_cfg_path is None:
        matches = list((CONFIG_ROOT / "preservation/generated").rglob(f"{game_key}.yaml"))
        if len(matches) != 1:
            raise SystemExit(f"Expected one generated config for {game_key}, found {len(matches)}")
        game_cfg_path = matches[0]

    game_cfg = resolve_game_config(load_yaml(game_cfg_path), agent_version="upload-dip-omeka-api.py")
    dip_path = Path(game_cfg["output_tar"])
    if not dip_path.is_file():
        raise SystemExit(f"DIP not found: {dip_path}")

    api_cfg = load_yaml(CONFIG_ROOT / "omeka-uploader.yaml")["api"]
    cred_rel = Path(api_cfg["credentials_file"])
    if cred_rel.parts and cred_rel.parts[0] == "config":
        cred_rel = Path(*cred_rel.parts[1:])
    creds_path = CONFIG_ROOT / cred_rel
    if not creds_path.is_file():
        creds_path = REPO_ROOT / api_cfg["credentials_file"]
    if not creds_path.is_file():
        raise SystemExit(
            f"Missing {creds_path}; copy from omeka-api-credentials-local.yaml.example and create an API key."
        )
    creds = load_yaml(creds_path)
    for key in ("key_identity", "key_credential"):
        if not creds.get(key) or creds[key] == "REPLACE_ME":
            raise SystemExit(f"Set {key} in {creds_path}")

    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]

    title = normalize_display_title(game_cfg.get("omeka_item_title") or game_key)
    base = api_cfg["base_url"]
    session = requests.Session()

    site_id = resolve_site_id(session, base, creds, api_cfg["site_slug"])
    title_pid = resolve_property_id(session, base, creds, "dcterms:title")
    item_set_id = resolve_item_set_id(session, base, creds, api_cfg["item_set_title"])
    is_public = bool(api_cfg.get("default_is_public", False))

    conn = pg_connect(db_cfg)
    metadata_json = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT omeka_item_id, omeka_media_id, metadata_json, moby_status, block_reason FROM game_ingest WHERE game_key = %s",
                (game_key,),
            )
            row = cur.fetchone()
        if row:
            metadata_json = row[2]
            if isinstance(metadata_json, str):
                metadata_json = json.loads(metadata_json)
        if (
            row
            and row[0]
            and row[1]
            and item_has_dip_media(session, base, creds, int(row[0]))
        ):
            print(
                json.dumps(
                    {
                        "game_key": game_key,
                        "skipped": True,
                        "omeka_item_id": row[0],
                        "omeka_media_id": row[1],
                    },
                    indent=2,
                )
            )
            return
    finally:
        conn.close()

    extra: dict[str, list] = {}
    moby_cfg = load_yaml(CONFIG_ROOT / "mobygames.yaml").get("mobygames") or {}
    if metadata_json and uses_moby_catalog_data(metadata_json):
        append_moby_fields_to_payload(
            extra,
            metadata_json,
            moby_cfg,
            session=session,
            base=base,
            creds=creds,
            literal_value=literal_value,
            uri_value=uri_value,
            resolve_property_id=resolve_property_id,
        )

    item_id, media_id = create_item_with_dip(
        session,
        base,
        creds,
        title=title,
        site_id=site_id,
        item_set_id=item_set_id,
        is_public=is_public,
        title_property_id=title_pid,
        tar_path=dip_path,
        ingester=api_cfg.get("dip_ingester", "omeka_dip_package"),
        extra_values=extra or None,
    )

    conn = pg_connect(db_cfg)
    try:
        update_ledger(conn, game_key, item_id, media_id)
    finally:
        conn.close()

    if not item_has_dip_media(session, base, creds, item_id):
        raise RuntimeError(f"Post-upload verification failed for item {item_id}")

    print(
        json.dumps(
            {
                "game_key": game_key,
                "omeka_item_id": item_id,
                "omeka_media_id": media_id,
                "dip": str(dip_path),
                "tar_bytes": dip_path.stat().st_size,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
