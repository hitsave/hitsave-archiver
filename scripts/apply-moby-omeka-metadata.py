#!/usr/bin/env python3
"""Push ledger Moby metadata onto an existing Omeka item (partial API update)."""
from __future__ import annotations

import importlib.util
import json
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

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path("/config") if Path("/config/omeka-test/omeka-api.yaml").is_file() else REPO_ROOT / "config"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from moby_omeka_fields import append_moby_fields_to_payload  # noqa: E402
from moby_resolve import finalize_moby_metadata, moby_attribution_text, uses_moby_catalog_data  # noqa: E402

_upload_spec = importlib.util.spec_from_file_location(
    "upload_dip_omeka_api", REPO_ROOT / "scripts/upload-dip-omeka-api.py"
)
_upload = importlib.util.module_from_spec(_upload_spec)
assert _upload_spec.loader
_upload_spec.loader.exec_module(_upload)


def find_game_config(config_root: Path, game_key: str, batch_key: str | None) -> Path | None:
    if batch_key:
        candidate = config_root / "preservation/generated" / batch_key / f"{game_key}.yaml"
        if candidate.is_file():
            return candidate
    generated = config_root / "preservation/generated"
    if generated.is_dir():
        matches = list(generated.rglob(f"{game_key}.yaml"))
        if len(matches) == 1:
            return matches[0]
        if matches:
            return sorted(matches)[0]
    return None


def item_title_values(item_body: dict) -> list[dict]:
    rows = item_body.get("dcterms:title") or []
    return [row for row in rows if (row.get("@value") or "").strip()]


def main() -> None:
    game_key = sys.argv[1]
    ingest_cfg = _upload.load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db = _upload.load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    api_cfg = _upload.load_yaml(CONFIG_ROOT / "omeka-test/omeka-api.yaml")["api"]
    cred_rel = Path(api_cfg["credentials_file"])
    if cred_rel.parts and cred_rel.parts[0] == "config":
        cred_rel = Path(*cred_rel.parts[1:])
    creds = _upload.load_yaml(CONFIG_ROOT / cred_rel)

    conn = _upload.pg_connect(db)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT omeka_item_id, metadata_json FROM game_ingest WHERE game_key = %s",
            (game_key,),
        )
        row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        raise SystemExit(f"No omeka_item_id for {game_key}")
    metadata_json = row[1]
    batch_key = None
    if game_key.count("-") >= 3:
        batch_key = "-".join(game_key.split("-")[:3])
    if isinstance(metadata_json, str):
        metadata_json = json.loads(metadata_json)
    if not uses_moby_catalog_data(metadata_json):
        raise SystemExit(f"No Moby catalog data for {game_key}")

    finalize_moby_metadata(metadata_json)

    item_id = int(row[0])
    base = api_cfg["base_url"]
    session = requests.Session()
    moby_cfg = _upload.load_yaml(CONFIG_ROOT / "mobygames.yaml").get("mobygames") or {}
    credit = moby_attribution_text(metadata_json, moby_cfg)

    get_url = _upload.api_url(base, f"items/{item_id}", creds)
    item_resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not item_resp.ok:
        raise SystemExit(f"GET item failed ({item_resp.status_code}): {item_resp.text[:800]}")
    item_body = item_resp.json()

    payload: dict = {}

    if not item_title_values(item_body):
        title = None
        game_cfg_path = find_game_config(CONFIG_ROOT, game_key, batch_key)
        if game_cfg_path:
            game_cfg = _upload.load_yaml(game_cfg_path)
            title = (game_cfg.get("omeka_item_title") or "").strip()
        if not title:
            title = (metadata_json.get("moby_title") or "").strip()
        if title:
            payload["dcterms:title"] = [
                _upload.literal_value(
                    _upload.resolve_property_id(session, base, creds, "dcterms:title"),
                    title,
                )
            ]

    append_moby_fields_to_payload(
        payload,
        metadata_json,
        moby_cfg,
        session=session,
        base=base,
        creds=creds,
        literal_value=_upload.literal_value,
        uri_value=_upload.uri_value,
        resolve_property_id=_upload.resolve_property_id,
    )

    q = urlencode(
        {
            "key_identity": creds["key_identity"],
            "key_credential": creds["key_credential"],
            "isPartial": "1",
        }
    )
    url = f"{base.rstrip('/')}/items/{item_id}?{q}"
    resp = session.patch(
        url,
        headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if not resp.ok:
        raise SystemExit(f"PATCH failed ({resp.status_code}): {resp.text[:1500]}")
    print(
        json.dumps(
            {
                "game_key": game_key,
                "omeka_item_id": item_id,
                "moby_game_id": metadata_json.get("moby_game_id"),
                "attribution": credit,
                "title_restored": "dcterms:title" in payload,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
