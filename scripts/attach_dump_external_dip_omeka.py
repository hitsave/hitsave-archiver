#!/usr/bin/env python3
"""Attach omeka_dip_package media to catalog-only dump items (Wasabi BagIt prefix)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import psycopg
    import requests
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from dip_mount_preflight import DipNotAvailableError, assert_bagit_external_dip_available  # noqa: E402
from omeka_catalog_common import load_register_dip_omeka_api  # noqa: E402
from omeka_dip_media import find_dip_media_id_on_item  # noqa: E402
from omeka_external_dip import attach_external_dip_media, resolve_bagit_external_dip  # noqa: E402
from omeka_staff_metadata import (  # noqa: E402
    load_omeka_catalog_config,
    require_external_dip_config,
    require_omeka_api_config,
)
from upload_bag_wasabi import pg_connect  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))


def _external_dip_helpers():
    import importlib.util

    path = REPO_ROOT / "scripts" / "apply-external-dip-omeka.py"
    spec = importlib.util.spec_from_file_location("apply_external_dip_omeka", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def attach_dump_dip(game_key: str) -> dict:
    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    catalog_cfg = load_omeka_catalog_config(CONFIG_ROOT)
    api_cfg = require_omeka_api_config(catalog_cfg)
    dip_cfg = require_external_dip_config(catalog_cfg)
    cred_path = CONFIG_ROOT / api_cfg["credentials_file"]
    if cred_path.parts and cred_path.parts[0] == "config":
        cred_path = CONFIG_ROOT / Path(*cred_path.parts[1:])
    if not cred_path.is_file():
        cred_path = REPO_ROOT / api_cfg["credentials_file"]
    creds = load_yaml(cred_path)

    api = load_register_dip_omeka_api()
    ext = _external_dip_helpers()
    dip_ingester = dip_cfg["dip_ingester"]
    is_public = api_cfg["default_is_public"]
    base = api_cfg["base_url"].rstrip("/")
    session = requests.Session()

    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT omeka_item_id, omeka_media_id, status, total_bytes,
                       package_storage_prefix, ingest_kind
                FROM game_ingest WHERE game_key = %s
                """,
                (game_key,),
            )
            row = cur.fetchone()
    if not row:
        return {"game_key": game_key, "skipped": True, "reason": "no ledger row"}
    item_id, ledger_media_id, status, total_bytes, ledger_prefix, ingest_kind = row
    if status != "complete":
        return {"game_key": game_key, "skipped": True, "reason": f"status={status}"}
    if not item_id:
        return {"game_key": game_key, "skipped": True, "reason": "no omeka_item_id"}
    if str(ingest_kind or "") != "dump":
        return {"game_key": game_key, "skipped": True, "reason": f"ingest_kind={ingest_kind}"}

    item_id = int(item_id)
    external_dip = resolve_bagit_external_dip(
        game_key, ingest_cfg, dip_storage=dip_cfg["dip_storage"]
    )
    package_storage_prefix = str(ledger_prefix or external_dip["package_storage_prefix"] or "").strip()
    if not package_storage_prefix:
        return {"game_key": game_key, "skipped": True, "reason": "empty package_storage_prefix"}

    try:
        assert_bagit_external_dip_available(
            ingest_cfg=ingest_cfg,
            catalog_cfg=catalog_cfg,
            config_root=CONFIG_ROOT,
            package_storage_prefix=package_storage_prefix,
            ingest_kind="dump",
        )
    except DipNotAvailableError as exc:
        return {"game_key": game_key, "skipped": True, "reason": str(exc)}

    dip_media_id = find_dip_media_id_on_item(
        session,
        base,
        creds,
        item_id,
        dip_ingester,
        api_url=api.api_url,
    )
    total_bytes = int(total_bytes) if total_bytes is not None else 0
    s3_uri = str(external_dip["s3_uri"])

    if dip_media_id:
        ext.patch_media_external_dip(
            session,
            base,
            creds,
            dip_media_id,
            s3_uri=s3_uri,
            package_bytes=total_bytes,
            sha256="",
            package_storage_prefix=package_storage_prefix,
            dip_storage=str(external_dip["dip_storage"]),
            api=api,
        )
        media_id = dip_media_id
        action = "patched"
    else:
        media_id = attach_external_dip_media(
            session,
            base,
            creds,
            item_id=item_id,
            s3_uri=s3_uri,
            ingester=dip_ingester,
            is_public=is_public,
            package_bytes=total_bytes,
            sha256="",
            package_storage_prefix=package_storage_prefix,
            dip_storage=str(external_dip["dip_storage"]),
            api_url=api.api_url,
            merge_media_data_json=ext.merge_media_data_json,
        )
        action = "attached"

    with pg_connect(db_cfg) as conn:
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

    return {
        "game_key": game_key,
        "omeka_item_id": item_id,
        "omeka_media_id": media_id,
        "action": action,
        "package_storage_prefix": package_storage_prefix,
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: attach_dump_external_dip_omeka.py <game_key> [game_key ...]")
    results = []
    for key in sys.argv[1:]:
        results.append(attach_dump_dip(key.strip()))
    print(json.dumps(results if len(results) > 1 else results[0], indent=2))


if __name__ == "__main__":
    main()
