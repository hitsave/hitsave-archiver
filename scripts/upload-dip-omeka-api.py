#!/usr/bin/env python3
"""Create Omeka catalog items; BagIt press packages use Wasabi S3 prefix only (no file upload)."""
from __future__ import annotations

import json
import os
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
CONFIG_ROOT = Path(os.environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from ingest_kind import resolve_ingest_kind, resolve_omeka_moby_profile  # noqa: E402
from moby_omeka_fields import append_moby_fields_to_payload  # noqa: E402
from moby_resolve import moby_description_for_omeka, uses_moby_catalog_data  # noqa: E402
from press_material_description import press_material_description  # noqa: E402
from normalize_display_title import catalog_item_title  # noqa: E402
from omeka_dip_media import find_dip_media_id_on_item  # noqa: E402
from omeka_staff_metadata import apply_item_aip_uri  # noqa: E402
from omeka_external_dip import (  # noqa: E402
    attach_external_dip_media,
    create_item_without_media,
    resolve_bagit_external_dip,
)
from preservation_common import resolve_game_config  # noqa: E402
from dip_mount_preflight import (  # noqa: E402
    DipNotAvailableError,
    assert_bagit_external_dip_available,
)


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _external_dip_helpers():
    import importlib.util

    path = REPO_ROOT / "scripts" / "apply-external-dip-omeka.py"
    spec = importlib.util.spec_from_file_location("apply_external_dip_omeka", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def api_url(base: str, path: str, creds: dict) -> str:
    q = urlencode(
        {
            "key_identity": creds["key_identity"],
            "key_credential": creds["key_credential"],
        }
    )
    return f"{base.rstrip('/')}/{path.lstrip('/')}?{q}"


def delete_item(
    session: requests.Session,
    base: str,
    creds: dict,
    item_id: int,
    *,
    api_url_fn=api_url,
) -> None:
    """Best-effort delete when media attach fails after item create."""
    url = api_url_fn(base, f"items/{item_id}", creds)
    session.delete(url, headers={"Accept": "application/ld+json"}, timeout=120)


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


def uri_value(property_id: int, value: str, *, is_public: bool | None = None) -> dict:
    row: dict = {"property_id": property_id, "type": "uri", "@id": value}
    if is_public is not None:
        row["is_public"] = is_public
    return row


def item_rdf_value_properties(item_body: dict) -> dict[str, list]:
    """RDF value properties on an item (dcterms:*, bibo:*, …), for merge-before-PATCH."""
    out: dict[str, list] = {}
    for key, rows in item_body.items():
        if not key or key.startswith("o:") or key.startswith("@"):
            continue
        if ":" not in key or not isinstance(rows, list) or not rows:
            continue
        if not isinstance(rows[0], dict):
            continue
        if "@value" in rows[0] or "@id" in rows[0] or "property_id" in rows[0]:
            out[key] = rows
    return out


def patch_item_rdf_values(
    session: requests.Session,
    base: str,
    creds: dict,
    item_id: int,
    updates: dict[str, list],
    *,
    item_body: dict | None = None,
) -> dict[str, list]:
    """
    PATCH RDF property values without dropping other properties.

    Omeka S treats all value properties as one unit: any PATCH that sets one
    dcterms:/bibo: property replaces the full value set unless existing values
    are merged in (see Omeka REST API partial update notes).
    """
    if not updates:
        return {}
    if item_body is None:
        get_url = api_url(base, f"items/{item_id}", creds)
        resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
        if not resp.ok:
            raise RuntimeError(f"GET item {item_id} failed ({resp.status_code}): {resp.text[:800]}")
        item_body = resp.json()
    existing = item_rdf_value_properties(item_body)
    payload = {**existing, **updates}
    patch_url = api_url(base, f"items/{item_id}", creds)
    patch = session.patch(
        patch_url,
        headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if not patch.ok:
        raise RuntimeError(f"Item values PATCH failed ({patch.status_code}): {patch.text[:2000]}")
    return payload


def resolve_omeka_item_title(game_key: str, metadata_json: dict | None) -> str:
    matches = list((CONFIG_ROOT / "preservation/generated").rglob(f"{game_key}.yaml"))
    if matches:
        gcfg = load_yaml(matches[0])
        title = (gcfg.get("omeka_item_title") or "").strip()
        if title:
            return catalog_item_title(title)
    meta = metadata_json or {}
    search = (meta.get("search_title") or "").strip()
    if search:
        return catalog_item_title(search)
    return catalog_item_title(game_key.replace("-", " "))


def item_has_dcterms_title(item_body: dict) -> bool:
    for row in item_body.get("dcterms:title") or []:
        if (row.get("@value") or "").strip():
            return True
    return bool((item_body.get("o:title") or "").strip())


def item_has_dip_package_media(
    session: requests.Session,
    base: str,
    creds: dict,
    item_id: int,
    dip_ingester: str,
) -> bool:
    return (
        find_dip_media_id_on_item(
            session,
            base,
            creds,
            item_id,
            dip_ingester,
            api_url=api_url,
        )
        is not None
    )


def apply_moby_metadata_to_item(
    session: requests.Session,
    base: str,
    creds: dict,
    item_id: int,
    metadata_json: dict,
    moby_cfg: dict,
    *,
    game_key: str | None = None,
    inventory_json: dict | None = None,
    omeka_profile: str = "press_material",
) -> list[str]:
    """PATCH Moby catalog fields onto an existing Omeka item (merge RDF values first)."""
    get_url = api_url(base, f"items/{item_id}", creds)
    resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"GET item {item_id} failed ({resp.status_code}): {resp.text[:800]}")
    item_body = resp.json()

    game_title = resolve_omeka_item_title(game_key, metadata_json) if game_key else None
    extra: dict[str, list] = {}
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
        game_title=game_title,
        inventory_json=inventory_json,
        item_body=item_body,
        omeka_profile=omeka_profile,
    )
    if game_key and not item_has_dcterms_title(item_body):
        title = resolve_omeka_item_title(game_key, metadata_json)
        title_pid = resolve_property_id(session, base, creds, "dcterms:title")
        extra["dcterms:title"] = [literal_value(title_pid, title)]
    if not extra:
        return []
    patch_item_rdf_values(session, base, creds, item_id, extra, item_body=item_body)
    return sorted(extra.keys())


class OmekaItemApi:
    api_url = staticmethod(api_url)
    resolve_property_id = staticmethod(resolve_property_id)
    patch_item_rdf_values = staticmethod(patch_item_rdf_values)


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
        raise SystemExit(
            "Usage: upload-dip-omeka-api.py <game_key> [path/to/game.yaml]\n"
            "       upload-dip-omeka-api.py path/to/game.yaml  (ledger resolves package_id)"
        )

    game_cfg_path: Path | None = None
    game_key: str | None = None
    if len(sys.argv) == 2 and sys.argv[1].endswith((".yaml", ".yml")):
        game_cfg_path = Path(sys.argv[1])
    else:
        game_key = sys.argv[1]
        game_cfg_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
        if game_cfg_path is None:
            matches = list((CONFIG_ROOT / "preservation/generated").rglob(f"{game_key}.yaml"))
            if len(matches) != 1:
                raise SystemExit(f"Expected one generated config for {game_key}, found {len(matches)}")
            game_cfg_path = matches[0]

    game_cfg = resolve_game_config(load_yaml(game_cfg_path), agent_version="upload-dip-omeka-api.py")
    omeka_profile = resolve_omeka_moby_profile(game_cfg=game_cfg, config_root=CONFIG_ROOT)

    ingest_cfg = load_yaml(CONFIG_ROOT / "preservation/ingest.yaml")
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    source_path = str(Path(game_cfg["source_game_folder"]).resolve())
    conn = pg_connect(db_cfg)
    ledger_kind: str | None = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, ingest_kind
                FROM game_ingest
                WHERE source_path = %s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (source_path,),
            )
            row = cur.fetchone()
        if row:
            game_key = str(row[0])
            ledger_kind = row[1]
    finally:
        conn.close()

    if not game_key:
        game_key = str(game_cfg.get("game_key") or "")
    if not game_key:
        raise SystemExit(f"No ledger row for source {source_path}; run ingest first")

    ingest_kind = resolve_ingest_kind(game_cfg)
    if ingest_kind == "dump":
        from apply_dump_catalog_omeka import apply_dump_catalog  # noqa: WPS433

        result = apply_dump_catalog(game_key=game_key, game_cfg_path=game_cfg_path)
        print(json.dumps(result, indent=2))
        return

    uploader_cfg = load_yaml(CONFIG_ROOT / "omeka-uploader.yaml")
    api_cfg = uploader_cfg["api"]
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

    kind = (str(ledger_kind).strip() if ledger_kind else "") or str(game_cfg.get("ingest_kind") or "")
    if kind:
        omeka_profile = resolve_omeka_moby_profile(ingest_kind=kind)

    title = catalog_item_title(game_cfg.get("omeka_item_title") or game_key)
    base = api_cfg["base_url"]
    session = requests.Session()

    site_id = resolve_site_id(session, base, creds, api_cfg["site_slug"])
    title_pid = resolve_property_id(session, base, creds, "dcterms:title")
    item_set_id = resolve_item_set_id(session, base, creds, api_cfg["item_set_title"])
    is_public = bool(api_cfg.get("default_is_public", False))

    package_storage_prefix = ""
    total_bytes = 0
    external_dip = resolve_bagit_external_dip(game_key, ingest_cfg)

    conn = pg_connect(db_cfg)
    metadata_json = None
    inventory_json = None
    ledger_item_id_for_upload: int | None = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT omeka_item_id, omeka_media_id, metadata_json, moby_status, block_reason, status,
                       inventory_json, ingest_kind, package_tar_key, package_tar_sha256, total_bytes,
                       package_storage_prefix
                FROM game_ingest WHERE game_key = %s
                """,
                (game_key,),
            )
            row = cur.fetchone()
        if row and row[5] and row[5] != "complete":
            print(
                json.dumps(
                    {
                        "game_key": game_key,
                        "skipped": True,
                        "reason": f"ledger status {row[5]!r} (ingest must be complete)",
                    },
                    indent=2,
                )
            )
            return
        if row:
            metadata_json = row[2]
            if isinstance(metadata_json, str):
                metadata_json = json.loads(metadata_json)
            inventory_json = row[6]
            if isinstance(inventory_json, str):
                inventory_json = json.loads(inventory_json)
            kind = (str(row[7]).strip() if row[7] else "") or str(game_cfg.get("ingest_kind") or "")
            if kind:
                omeka_profile = resolve_omeka_moby_profile(ingest_kind=kind)
            elif game_cfg.get("ingest_kind"):
                omeka_profile = resolve_omeka_moby_profile(game_cfg=game_cfg)
        if row:
            total_bytes = int(row[10]) if row[10] is not None else 0
            package_storage_prefix = str(row[11]).strip() if row[11] else ""
        if not package_storage_prefix:
            package_storage_prefix = str(external_dip.get("package_storage_prefix") or "")
        if not package_storage_prefix:
            raise SystemExit(
                f"BagIt package not on Wasabi for {game_key!r} "
                "(package_storage_prefix empty); run ingest first"
            )

        dip_ingester = str(api_cfg.get("dip_ingester", "omeka_dip_package"))
        moby_cfg = load_yaml(CONFIG_ROOT / "mobygames.yaml").get("mobygames") or {}
        existing_item_id = int(row[0]) if row and row[0] else None
        dip_media_id = None
        if existing_item_id:
            dip_media_id = find_dip_media_id_on_item(
                session,
                base,
                creds,
                existing_item_id,
                dip_ingester,
                api_url=api_url,
            )
        if row and existing_item_id and dip_media_id:
            if not row[1] or int(row[1]) != dip_media_id:
                update_ledger(conn, game_key, existing_item_id, dip_media_id)
            moby_applied: list[str] = []
            if metadata_json and uses_moby_catalog_data(metadata_json):
                moby_applied = apply_moby_metadata_to_item(
                    session,
                    base,
                    creds,
                    existing_item_id,
                    metadata_json,
                    moby_cfg,
                    game_key=game_key,
                    inventory_json=inventory_json,
                    omeka_profile=omeka_profile,
                )
            ext_dip = _external_dip_helpers()
            ext_dip.patch_media_external_dip(
                session,
                base,
                creds,
                dip_media_id,
                s3_uri=str(external_dip["s3_uri"]),
                package_bytes=total_bytes,
                sha256="",
                package_storage_prefix=str(external_dip["package_storage_prefix"]),
                api=OmekaItemApi,
            )
            staff_aip = apply_item_aip_uri(
                session,
                base,
                creds,
                game_key,
                existing_item_id,
                ingest_cfg=ingest_cfg,
                config_root=CONFIG_ROOT,
                api=OmekaItemApi,
            )
            print(
                json.dumps(
                    {
                        "game_key": game_key,
                        "skipped": True,
                        "omeka_item_id": existing_item_id,
                        "omeka_media_id": dip_media_id,
                        "moby_fields_applied": moby_applied,
                        "staff_aip_uri": staff_aip,
                    },
                    indent=2,
                )
            )
            return
        ledger_item_id_for_upload = existing_item_id
    finally:
        conn.close()

    moby_cfg = load_yaml(CONFIG_ROOT / "mobygames.yaml").get("mobygames") or {}
    extra: dict[str, list] = {}
    desc_pid = resolve_property_id(session, base, creds, "dcterms:description")
    if (
        omeka_profile == "game_catalog"
        and metadata_json
        and uses_moby_catalog_data(metadata_json)
    ):
        desc_text = moby_description_for_omeka(metadata_json, moby_cfg)
    else:
        desc_text = press_material_description(title, inventory_json)
    extra["dcterms:description"] = [literal_value(desc_pid, desc_text)]
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
            game_title=title,
            inventory_json=inventory_json,
            omeka_profile=omeka_profile,
        )

    dip_ingester = str(api_cfg.get("dip_ingester", "omeka_dip_package"))
    ext_dip = _external_dip_helpers()
    merge_media_data_json = ext_dip.merge_media_data_json

    try:
        assert_bagit_external_dip_available(
            ingest_cfg=ingest_cfg,
            uploader_cfg=uploader_cfg,
            config_root=CONFIG_ROOT,
            package_storage_prefix=package_storage_prefix,
        )
    except DipNotAvailableError as e:
        raise SystemExit(f"BagIt DIP not available for Omeka upload: {e}") from e

    if ledger_item_id_for_upload:
        item_id = ledger_item_id_for_upload
        if extra:
            patch_item_rdf_values(session, base, creds, item_id, extra)
        media_id = attach_external_dip_media(
            session,
            base,
            creds,
            item_id=item_id,
            s3_uri=str(external_dip["s3_uri"]),
            package_storage_prefix=str(external_dip["package_storage_prefix"]),
            dip_storage=str(external_dip.get("dip_storage") or "wasabi-s3"),
            ingester=dip_ingester,
            is_public=is_public,
            package_bytes=total_bytes,
            sha256="",
            api_url=api_url,
            merge_media_data_json=merge_media_data_json,
        )
    else:
        item_id = None
        try:
            item_id = create_item_without_media(
                session,
                base,
                creds,
                title=title,
                site_id=site_id,
                item_set_id=item_set_id,
                is_public=is_public,
                title_property_id=title_pid,
                extra_values=extra or None,
                literal_value=literal_value,
                api_url=api_url,
            )
            media_id = attach_external_dip_media(
                session,
                base,
                creds,
                item_id=item_id,
                s3_uri=str(external_dip["s3_uri"]),
                package_storage_prefix=str(external_dip["package_storage_prefix"]),
                dip_storage=str(external_dip.get("dip_storage") or "wasabi-s3"),
                ingester=dip_ingester,
                is_public=is_public,
                package_bytes=total_bytes,
                sha256="",
                api_url=api_url,
                merge_media_data_json=merge_media_data_json,
            )
        except Exception:
            if item_id is not None:
                delete_item(session, base, creds, item_id)
            raise

    conn = pg_connect(db_cfg)
    try:
        update_ledger(conn, game_key, item_id, media_id)
    finally:
        conn.close()

    if not item_has_dip_package_media(session, base, creds, item_id, dip_ingester):
        raise RuntimeError(f"Post-upload verification failed for item {item_id} (no DIP package media)")

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
            omeka_profile=omeka_profile,
        )

    staff_aip = apply_item_aip_uri(
        session,
        base,
        creds,
        game_key,
        item_id,
        ingest_cfg=ingest_cfg,
        config_root=CONFIG_ROOT,
        api=OmekaItemApi,
    )
    out = {
        "game_key": game_key,
        "omeka_item_id": item_id,
        "omeka_media_id": media_id,
        "package_storage_prefix": external_dip["package_storage_prefix"],
        "s3_uri": external_dip["s3_uri"],
        "package_bytes": total_bytes,
        "staff_aip_uri": staff_aip,
        "wasabi_only": True,
    }

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
