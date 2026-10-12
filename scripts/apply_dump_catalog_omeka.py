#!/usr/bin/env python3
"""Create or update an Omeka catalog item for ingest kind dump (Redump + Moby, no DIP tar)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

try:
    import psycopg
    import requests
    import yaml
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from dump_catalog_omeka import (  # noqa: E402
    build_redump_values,
    dump_catalog_config,
    manifest_from_sip_and_handoff,
)
from ingest_kind import resolve_ingest_kind  # noqa: E402
from moby_omeka_fields import (  # noqa: E402
    append_moby_fields_to_payload,
    apply_moby_metadata_to_item,
    uses_moby_catalog_data,
)
from moby_resolve import moby_description_for_omeka  # noqa: E402
from preservation_common import load_ingest_config, resolve_game_config  # noqa: E402

CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))


def _register_dip_api_module():
    path = SCRIPTS / "register-dip-omeka-api.py"
    spec = importlib.util.spec_from_file_location("register_dip_omeka_api", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def update_ledger_omeka(game_key: str, item_id: int) -> None:
    ingest = load_ingest_config()
    db_cfg = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE game_ingest
                SET omeka_item_id = %s,
                    omeka_media_id = NULL,
                    updated_at = NOW()
                WHERE game_key = %s
                """,
                (item_id, game_key),
            )
            if cur.rowcount == 0:
                raise RuntimeError(f"No game_ingest row for game_key={game_key!r}")
        conn.commit()


def ensure_item_set(session, base, creds, title: str, *, is_public: bool, api) -> int:
    try:
        item_set_id = api.resolve_item_set_id(session, base, creds, title)
    except RuntimeError:
        url = api.api_url(base, "item_sets", creds)
        resp = session.post(url, json={"o:title": title, "o:is_public": is_public}, timeout=120)
        if not resp.ok:
            raise RuntimeError(f"Create item set failed ({resp.status_code}): {resp.text[:800]}")
        item_set_id = int(resp.json()["o:id"])
    return item_set_id


def apply_dump_catalog(
    *,
    game_key: str,
    game_cfg_path: Path,
    handoff_path: Path | None = None,
    patch_item_id: int | None = None,
) -> dict[str, Any]:
    api = _register_dip_api_module()
    game_cfg = resolve_game_config(load_yaml(game_cfg_path), agent_version="apply-dump-catalog-omeka.py")
    if resolve_ingest_kind(game_cfg) != "dump":
        raise RuntimeError(f"ingest_kind must be dump for {game_cfg_path}")

    source = Path(game_cfg["source_game_folder"]).resolve()
    handoff = load_yaml(handoff_path) if handoff_path and handoff_path.is_file() else {}
    title_raw = str(game_cfg.get("omeka_item_title") or source.name)
    manifest = manifest_from_sip_and_handoff(
        source_dir=source,
        game_key=game_key,
        omeka_title=title_raw,
        handoff=handoff,
    )

    ingest_cfg = load_ingest_config()
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    metadata_json = None
    inventory_json = None
    ledger_aip: str | None = None
    ledger_prefix: str | None = None
    existing_item_id: int | None = patch_item_id

    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT omeka_item_id, metadata_json, inventory_json,
                       aip_wasabi_prefix, package_storage_prefix, status
                FROM game_ingest WHERE game_key = %s
                """,
                (game_key,),
            )
            row = cur.fetchone()
        if row:
            if row[5] and row[5] != "complete":
                return {"game_key": game_key, "skipped": True, "reason": f"ledger status {row[5]!r}"}
            if existing_item_id is None and row[0]:
                existing_item_id = int(row[0])
            metadata_json = row[1]
            inventory_json = row[2]
            if isinstance(metadata_json, str):
                metadata_json = json.loads(metadata_json)
            if isinstance(inventory_json, str):
                inventory_json = json.loads(inventory_json)
            ledger_aip = row[3]
            ledger_prefix = row[4]

    uploader_cfg = load_yaml(CONFIG_ROOT / "omeka-catalog.yaml")
    api_cfg = uploader_cfg.get("api") or {}
    dump_cfg = dump_catalog_config(uploader_cfg)
    cred_path = CONFIG_ROOT / str(api_cfg.get("credentials_file", "secrets/omeka-api-credentials-local.yaml"))
    creds = load_yaml(cred_path)
    base = str(api_cfg["base_url"]).rstrip("/")
    site_slug = str(api_cfg.get("site_slug", "hitsave-test"))
    item_set_title = str(dump_cfg.get("item_set_title", "Dumps"))
    is_public = bool(dump_cfg.get("is_public", True))
    storage_uri_base = str(dump_cfg.get("storage_uri_base", ""))

    session = requests.Session()
    site_id = api.resolve_site_id(session, base, creds, site_slug)
    moby_cfg = load_yaml(CONFIG_ROOT / "mobygames.yaml").get("mobygames") or {}

    existing_body = None
    if existing_item_id:
        resp = session.get(
            api.api_url(base, f"items/{existing_item_id}", creds),
            headers={"Accept": "application/ld+json"},
            timeout=120,
        )
        if resp.ok:
            existing_body = resp.json()

    rdf_values, catalog_title = build_redump_values(
        session,
        base,
        creds,
        manifest,
        storage_uri_base=storage_uri_base,
        api=api,
        redump_cfg=dump_cfg,
        existing_item=existing_body,
        ledger_aip_wasabi_prefix=ledger_aip,
        ledger_package_storage_prefix=ledger_prefix,
    )

    if metadata_json and uses_moby_catalog_data(metadata_json):
        desc = moby_description_for_omeka(metadata_json, moby_cfg)
        if desc:
            desc_pid = api.resolve_property_id(session, base, creds, "dcterms:description")
            rdf_values["dcterms:description"] = [api.literal_value(desc_pid, desc)]
        append_moby_fields_to_payload(
            rdf_values,
            metadata_json,
            moby_cfg,
            session=session,
            base=base,
            creds=creds,
            literal_value=api.literal_value,
            uri_value=api.uri_value,
            resolve_property_id=api.resolve_property_id,
            game_title=catalog_title,
            inventory_json=inventory_json,
            omeka_profile="game_catalog",
        )

    if existing_item_id:
        api.patch_item_rdf_values(session, base, creds, existing_item_id, rdf_values, item_body=existing_body)
        item_id = existing_item_id
    else:
        item_set_id = ensure_item_set(session, base, creds, item_set_title, is_public=is_public, api=api)
        payload: dict[str, Any] = {
            "o:is_public": is_public,
            "o:site": [{"o:id": site_id}],
            "o:item_set": [{"o:id": item_set_id}],
        }
        payload.update(rdf_values)
        resp = session.post(
            api.api_url(base, "items", creds),
            json=payload,
            headers={"Accept": "application/ld+json"},
            timeout=120,
        )
        if not resp.ok:
            raise RuntimeError(f"Create catalog item failed ({resp.status_code}): {resp.text[:2000]}")
        item_id = int(resp.json()["o:id"])

    if metadata_json and uses_moby_catalog_data(metadata_json):
        apply_moby_metadata_to_item(
            session,
            base,
            creds,
            item_id,
            metadata_json,
            moby_cfg,
            game_key=game_key,
            inventory_json=inventory_json,
            omeka_profile="game_catalog",
        )

    update_ledger_omeka(game_key, item_id)
    return {
        "game_key": game_key,
        "omeka_item_id": item_id,
        "title": catalog_title,
        "dump_class": manifest.get("dump_class"),
        "package_storage_prefix": manifest.get("package_storage_prefix"),
        "catalog_only": True,
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: apply-dump-catalog-omeka.py <game.yaml> [handoff.yaml] [--item-id ID]"
        )
    game_cfg_path = Path(sys.argv[1])
    handoff_path: Path | None = None
    patch_item_id: int | None = None
    extra = sys.argv[2:]
    idx = 0
    while idx < len(extra):
        if extra[idx] == "--item-id" and idx + 1 < len(extra):
            patch_item_id = int(extra[idx + 1])
            idx += 2
        elif extra[idx].endswith((".yaml", ".yml")) and extra[idx] != game_cfg_path:
            handoff_path = Path(extra[idx])
            idx += 1
        else:
            idx += 1

    game_cfg = resolve_game_config(load_yaml(game_cfg_path))
    game_key = str(game_cfg.get("game_key") or "")
    if not game_key:
        raise SystemExit("game_key missing from config")

    result = apply_dump_catalog(
        game_key=game_key,
        game_cfg_path=game_cfg_path,
        handoff_path=handoff_path,
        patch_item_id=patch_item_id,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
