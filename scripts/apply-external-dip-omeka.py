#!/usr/bin/env python3
"""Patch an existing Omeka DIP media row to read an unpacked BagIt prefix on Wasabi (S3)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    import requests
    import yaml
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from ingest_kind import resolve_omeka_moby_profile  # noqa: E402
from omeka_dip_media import find_dip_media_id_on_item, media_is_dip_package  # noqa: E402
from moby_resolve import uses_moby_catalog_data  # noqa: E402
from omeka_staff_metadata import (  # noqa: E402
    apply_item_aip_uri,
    load_omeka_catalog_config,
    require_external_dip_config,
    require_omeka_api_config,
    strip_public_wasabi_bibo_uri,
    strip_wasabi_s3_from_relation,
)
from package_ledger import ledger_bagit_wasabi_paths  # noqa: E402

CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))


from omeka_catalog_common import load_register_dip_omeka_api  # noqa: E402
from yaml_config import load_yaml  # noqa: E402


def patch_media_external_dip(
    session: requests.Session,
    base: str,
    creds: dict,
    media_id: int,
    *,
    s3_uri: str,
    package_bytes: int,
    sha256: str,
    package_storage_prefix: str,
    dip_storage: str,
    api,
) -> None:
    get_url = api.api_url(base, f"media/{media_id}", creds)
    resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"GET media {media_id} failed ({resp.status_code}): {resp.text[:800]}")
    body = resp.json()
    existing_data = body.get("data") if isinstance(body.get("data"), dict) else {}
    merged = {
        **existing_data,
        "dip_storage": dip_storage,
        "dip_wasabi_uri": s3_uri,
        "package_wasabi_uri": s3_uri,
        "dip_size_bytes": package_bytes,
        "package_storage_prefix": package_storage_prefix,
    }
    if sha256:
        merged["dip_sha256"] = sha256

    patch_url = api.api_url(base, f"media/{media_id}", creds)
    patch = session.patch(
        patch_url,
        headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
        json={"data": merged},
        timeout=120,
    )
    if not patch.ok:
        raise RuntimeError(f"PATCH media {media_id} failed ({patch.status_code}): {patch.text[:2000]}")

    verify = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    verify_data = (verify.json().get("data") or {}) if verify.ok else {}
    if verify_data.get("package_storage_prefix") == package_storage_prefix:
        return
    # Never patch Omeka's database behind the API (this used to `docker exec` into a MariaDB
    # container as root, and the configured container was the local test DB, not production).
    raise RuntimeError(
        f"Omeka API did not persist media.data.package_storage_prefix on media {media_id} "
        f"(expected {package_storage_prefix!r}); fix the Omeka module/API, then re-run."
    )


def ledger_omeka_moby_row(
    game_key: str, ingest_cfg: dict
) -> tuple[int, int, dict | None, dict | None, str | None]:
    import json

    import psycopg

    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    conn = psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT omeka_item_id, omeka_media_id, metadata_json, inventory_json, ingest_kind
                FROM game_ingest WHERE game_key = %s
                """,
                (game_key,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row[0] or not row[1]:
        raise RuntimeError(f"No omeka_item_id/omeka_media_id in ledger for {game_key!r}")
    metadata_json = row[2]
    inventory_json = row[3]
    if isinstance(metadata_json, str):
        metadata_json = json.loads(metadata_json)
    if isinstance(inventory_json, str):
        inventory_json = json.loads(inventory_json)
    ingest_kind = str(row[4]) if row[4] else None
    return int(row[0]), int(row[1]), metadata_json, inventory_json, ingest_kind


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_key")
    parser.add_argument(
        "bag_dir",
        type=Path,
        nargs="?",
        default=Path("."),
        help="Local bag directory (optional; used only for size hint)",
    )
    parser.add_argument("--item-id", type=int, default=0)
    parser.add_argument("--media-id", type=int, default=0)
    args = parser.parse_args()

    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    ingest_wasabi = ingest_cfg.get("wasabi") or {}
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    s3_uri, package_storage_prefix = ledger_bagit_wasabi_paths(
        db_cfg, args.game_key, ingest_wasabi
    )
    package_bytes = 0
    if args.bag_dir.is_dir():
        package_bytes = sum(
            p.stat().st_size for p in args.bag_dir.rglob("*") if p.is_file()
        )

    catalog_cfg = load_omeka_catalog_config(CONFIG_ROOT)
    api_cfg = require_omeka_api_config(catalog_cfg)
    dip_cfg = require_external_dip_config(catalog_cfg)
    cred_path = CONFIG_ROOT / api_cfg["credentials_file"]
    creds = load_yaml(cred_path)
    base = api_cfg["base_url"].rstrip("/")

    api = load_register_dip_omeka_api()
    session = requests.Session()

    metadata_json: dict | None = None
    inventory_json: dict | None = None
    ingest_kind: str | None = None
    if args.item_id and args.media_id:
        item_id, media_id = args.item_id, args.media_id
    else:
        item_id, media_id, metadata_json, inventory_json, ingest_kind = ledger_omeka_moby_row(
            args.game_key, ingest_cfg
        )

    dip_ingester = dip_cfg["dip_ingester"]
    dip_media_id = find_dip_media_id_on_item(
        session,
        base,
        creds,
        item_id,
        dip_ingester,
        api_url=api.api_url,
    )
    if dip_media_id is not None:
        media_id = dip_media_id
    else:
        check_url = api.api_url(base, f"media/{media_id}", creds)
        check = session.get(check_url, headers={"Accept": "application/ld+json"}, timeout=120)
        if not check.ok or not media_is_dip_package(check.json(), dip_ingester=dip_ingester):
            raise RuntimeError(
                f"Item {item_id} has no omeka_dip_package media; "
                f"ledger omeka_media_id={media_id} — run register-dip-omeka-api to attach one"
            )

    patch_media_external_dip(
        session,
        base,
        creds,
        media_id,
        s3_uri=s3_uri,
        package_bytes=package_bytes,
        sha256="",
        package_storage_prefix=package_storage_prefix,
        dip_storage=dip_cfg["dip_storage"],
        api=api,
    )
    staff_aip_uri = apply_item_aip_uri(
        session,
        base,
        creds,
        args.game_key,
        item_id,
        ingest_cfg=ingest_cfg,
        config_root=CONFIG_ROOT,
        api=api.OmekaItemApi,
    )
    strip_public_wasabi_bibo_uri(session, base, creds, item_id, api=api.OmekaItemApi)
    if not staff_aip_uri:
        strip_wasabi_s3_from_relation(session, base, creds, item_id, api=api.OmekaItemApi)

    moby_fields: list[str] = []
    moby_url: str | None = None
    if metadata_json and uses_moby_catalog_data(metadata_json):
        from moby_resolve import load_mobygames_config, moby_public_game_url

        moby_cfg = load_mobygames_config(CONFIG_ROOT)
        omeka_profile = resolve_omeka_moby_profile(
            ingest_kind=ingest_kind,
            game_key=args.game_key,
            config_root=CONFIG_ROOT,
        )
        moby_fields = api.apply_moby_metadata_to_item(
            session,
            base,
            creds,
            item_id,
            metadata_json,
            moby_cfg,
            game_key=args.game_key,
            inventory_json=inventory_json,
            omeka_profile=omeka_profile,
        )
        moby_url = metadata_json.get("moby_url") or (
            moby_public_game_url(moby_cfg, metadata_json["moby_game_id"])
            if metadata_json.get("moby_game_id")
            else None
        )

    print(
        json.dumps(
            {
                "game_key": args.game_key,
                "omeka_item_id": item_id,
                "omeka_media_id": media_id,
                "package_storage_prefix": package_storage_prefix,
                "s3_uri": s3_uri,
                "staff_aip_uri": staff_aip_uri,
                "moby_url": moby_url,
                "moby_fields_applied": moby_fields,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
