#!/usr/bin/env python3
"""Patch an existing Omeka DIP media row to read an unpacked BagIt prefix on Wasabi (S3)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

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
    strip_public_wasabi_bibo_uri,
    strip_wasabi_s3_from_relation,
)
from package_ledger import ledger_bagit_wasabi_paths  # noqa: E402

CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _upload_api_module():
    import importlib.util

    path = SCRIPTS / "upload-dip-omeka-api.py"
    spec = importlib.util.spec_from_file_location("upload_dip_omeka_api", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
        "dip_storage": "wasabi-s3",
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

    merge_media_data_json(media_id, merged)


def resolve_omeka_mariadb_container() -> str:
    env = os.environ.get("OMEKA_MARIADB_CONTAINER", "").strip()
    if env:
        return env
    uploader = load_yaml(CONFIG_ROOT / "omeka-uploader.yaml")
    db = uploader.get("omeka_mariadb") or {}
    return str(db.get("container") or "").strip()


def merge_media_data_json(media_id: int, merged: dict[str, Any]) -> None:
    container = resolve_omeka_mariadb_container()
    uploader = load_yaml(CONFIG_ROOT / "omeka-uploader.yaml")
    database = str(
        os.environ.get("OMEKA_MARIADB_DATABASE")
        or (uploader.get("omeka_mariadb") or {}).get("database")
        or "omeka_s"
    ).strip()
    if not container:
        raise RuntimeError(
            "Omeka API did not persist media.data.package_storage_prefix; "
            "set OMEKA_MARIADB_CONTAINER (e.g. hitsave-omeka-test-mariadb-1) and re-run."
        )
    patch_json = json.dumps(merged, separators=(",", ":"))
    sql = (
        f"UPDATE media SET data = JSON_MERGE_PATCH(COALESCE(data, '{{}}'), "
        f"'{patch_json.replace(chr(39), chr(39) + chr(39))}') WHERE id = {int(media_id)};"
    )
    cmd = [
        "docker",
        "exec",
        container,
        "sh",
        "-c",
        f'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" {database} -e {json.dumps(sql)}',
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"MariaDB media.data merge failed ({proc.returncode}): {proc.stderr[:800]}"
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

    uploader_cfg = load_yaml(CONFIG_ROOT / "omeka-uploader.yaml")
    api_cfg = uploader_cfg.get("api") or {}
    cred_path = CONFIG_ROOT / str(api_cfg.get("credentials_file", "secrets/omeka-api-credentials-local.yaml"))
    creds = load_yaml(cred_path)
    base = str(api_cfg["base_url"]).rstrip("/")

    api = _upload_api_module()
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

    dip_ingester = str(api_cfg.get("dip_ingester", "omeka_dip_package"))
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
                f"ledger omeka_media_id={media_id} — run upload-dip-omeka-api to attach one"
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
        moby_cfg = load_yaml(CONFIG_ROOT / "mobygames.yaml").get("mobygames") or {}
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
            f"https://www.mobygames.com/game/{metadata_json['moby_game_id']}"
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
