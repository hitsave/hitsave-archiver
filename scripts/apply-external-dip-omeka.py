#!/usr/bin/env python3
"""Point an existing Omeka DIP media row at a read-only external .tar (Wasabi mount path)."""
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
from omeka_staff_metadata import apply_item_aip_uri  # noqa: E402

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
    external_tar_path: str,
    s3_uri: str,
    tar_bytes: int,
    sha256: str,
    api,
) -> None:
    get_url = api.api_url(base, f"media/{media_id}", creds)
    resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"GET media {media_id} failed ({resp.status_code}): {resp.text[:800]}")
    body = resp.json()
    # Omeka S REST uses "data" (not "o:data") for media JSON storage.
    existing_data = body.get("data") if isinstance(body.get("data"), dict) else {}
    merged = {
        **existing_data,
        "external_tar_path": external_tar_path,
        "dip_storage": "wasabi-mount",
        "dip_wasabi_uri": s3_uri,
        "dip_size_bytes": tar_bytes,
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
    if verify_data.get("external_tar_path") == external_tar_path:
        return

    # Omeka S REST PATCH accepts "data" but drops unknown keys; merge via MariaDB when configured.
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
            "Omeka API did not persist media.data.external_tar_path; "
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


def patch_item_staff_uri(
    session: requests.Session,
    base: str,
    creds: dict,
    item_id: int,
    s3_uri: str,
    *,
    api,
) -> None:
    get_url = api.api_url(base, f"items/{item_id}", creds)
    resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"GET item {item_id} failed ({resp.status_code}): {resp.text[:800]}")
    item_body = resp.json()
    pid = api.resolve_property_id(session, base, creds, "bibo:uri")
    updates: dict[str, list] = {"bibo:uri": [api.uri_value(pid, s3_uri)]}
    api.patch_item_rdf_values(session, base, creds, item_id, updates, item_body=item_body)


def resolve_ids_from_ledger(game_key: str, ingest_cfg: dict) -> tuple[int, int]:
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
                "SELECT omeka_item_id, omeka_media_id FROM game_ingest WHERE game_key = %s",
                (game_key,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row[0] or not row[1]:
        raise RuntimeError(f"No omeka_item_id/omeka_media_id in ledger for {game_key!r}")
    return int(row[0]), int(row[1])


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_key")
    parser.add_argument("dip_tar", type=Path, help="Local DIP .tar to upload")
    parser.add_argument(
        "--dip-wasabi-config",
        type=Path,
        default=CONFIG_ROOT / "preservation/dip-wasabi.yml",
        help="dip-wasabi.yml (falls back to .example)",
    )
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--item-id", type=int, default=0)
    parser.add_argument("--media-id", type=int, default=0)
    args = parser.parse_args()

    import importlib.util

    up_path = SCRIPTS / "upload-dip-wasabi.py"
    spec = importlib.util.spec_from_file_location("upload_dip_wasabi", up_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {up_path}")
    upload_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upload_mod)

    cfg_path = upload_mod.dip_wasabi_config_path(
        args.dip_wasabi_config if args.dip_wasabi_config.is_file() else None
    )

    if args.skip_upload:
        wasabi = load_yaml(cfg_path).get("dip_wasabi") or {}
        tar_name = args.dip_tar.name
        object_key = upload_mod.object_key
        omeka_external_tar_path = upload_mod.omeka_external_tar_path

        bucket = str(wasabi["bucket"])
        key = object_key(str(wasabi.get("prefix", "")), args.game_key, tar_name)
        result: dict[str, Any] = {
            "s3_uri": f"s3://{bucket}/{key}",
            "external_tar_path": omeka_external_tar_path(wasabi, args.game_key, tar_name),
            "tar_bytes": args.dip_tar.stat().st_size if args.dip_tar.is_file() else 0,
            "sha256": "",
        }
    else:
        result = upload_mod.upload_dip_tar(args.dip_tar, args.game_key, cfg_path=cfg_path)

    uploader_cfg = load_yaml(CONFIG_ROOT / "omeka-uploader.yaml")
    api_cfg = uploader_cfg.get("api") or {}
    cred_path = CONFIG_ROOT / str(api_cfg.get("credentials_file", "secrets/omeka-api-credentials-local.yaml"))
    creds = load_yaml(cred_path)
    base = str(api_cfg["base_url"]).rstrip("/")
    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")

    api = _upload_api_module()
    session = requests.Session()

    if args.item_id and args.media_id:
        item_id, media_id = args.item_id, args.media_id
    else:
        item_id, media_id = resolve_ids_from_ledger(args.game_key, ingest_cfg)

    patch_media_external_dip(
        session,
        base,
        creds,
        media_id,
        external_tar_path=str(result["external_tar_path"]),
        s3_uri=str(result["s3_uri"]),
        tar_bytes=int(result["tar_bytes"]),
        sha256=str(result.get("sha256") or ""),
        api=api,
    )
    patch_item_staff_uri(session, base, creds, item_id, str(result["s3_uri"]), api=api)

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

    print(
        json.dumps(
            {
                "game_key": args.game_key,
                "omeka_item_id": item_id,
                "omeka_media_id": media_id,
                "external_tar_path": result["external_tar_path"],
                "s3_uri": result["s3_uri"],
                "staff_aip_uri": staff_aip_uri,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
