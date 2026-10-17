#!/usr/bin/env python3
"""Create or update an Omeka catalog item for ingest kind dump (Redump + Moby metadata)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    import psycopg
    import requests
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
from moby_omeka_fields import append_moby_fields_to_payload  # noqa: E402
from moby_resolve import (  # noqa: E402
    load_mobygames_config,
    moby_description_for_omeka,
    uses_moby_catalog_data,
)
from omeka_catalog_common import (  # noqa: E402
    apply_item_item_sets,
    apply_item_resource_template,
    load_register_dip_omeka_api,
    resolve_dump_item_set_id,
    resolve_resource_template,
    set_resource_public,
)
from omeka_staff_metadata import load_omeka_catalog_config, require_omeka_api_config  # noqa: E402
from preservation_common import load_ingest_config, resolve_game_config  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))


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


def _post_catalog_item(
    session: requests.Session,
    base: str,
    creds: dict,
    *,
    site_id: int,
    item_set_id: int,
    resource_template_id: int | None,
    resource_class_id: int | None,
    is_public: bool,
    rdf_values: dict[str, Any],
    api,
) -> int:
    payload: dict[str, Any] = {
        "o:is_public": is_public,
        "o:site": [{"o:id": site_id}],
        "o:item_set": [{"o:id": item_set_id}],
    }
    if resource_template_id is not None:
        payload["o:resource_template"] = {"o:id": resource_template_id}
    if resource_class_id is not None:
        payload["o:resource_class"] = {"o:id": resource_class_id}
    payload.update(rdf_values)
    resp = session.post(
        api.api_url(base, "items", creds),
        json=payload,
        headers={"Accept": "application/ld+json"},
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"Create catalog item failed ({resp.status_code}): {resp.text[:2000]}")
    return int(resp.json()["o:id"])


def apply_handoff_dump_catalog(
    manifest: dict[str, Any],
    *,
    config_root: Path | None = None,
    patch_item_id: int | None = None,
) -> dict[str, Any]:
    """Metadata-only dump catalog item from GDrive/handoff YAML (no ledger Moby)."""
    root = config_root or CONFIG_ROOT
    uploader_cfg = load_omeka_catalog_config(root)
    api_cfg = require_omeka_api_config(uploader_cfg)
    dump_cfg = dump_catalog_config(uploader_cfg)
    creds = load_yaml(root / api_cfg["credentials_file"])

    base = api_cfg["base_url"].rstrip("/")
    site_slug = api_cfg["site_slug"]
    item_set_title = dump_cfg["item_set_title"]
    is_public = dump_cfg["is_public"]
    storage_uri_base = dump_cfg["storage_uri_base"]
    ingest_cfg = load_ingest_config()
    wasabi_ingest_cfg = ingest_cfg.get("wasabi")

    api = load_register_dip_omeka_api()
    session = requests.Session()
    site_id = api.resolve_site_id(session, base, creds, site_slug)
    resource_template_id, resource_class_id = resolve_resource_template(
        session, base, creds, dump_cfg["resource_template_label"], api=api
    )
    item_set_id = resolve_dump_item_set_id(
        session, base, creds, dump_cfg, is_public=is_public, api=api
    )

    if patch_item_id is not None:
        resp = session.get(
            api.api_url(base, f"items/{patch_item_id}", creds),
            headers={"Accept": "application/ld+json"},
            timeout=120,
        )
        if not resp.ok:
            raise RuntimeError(f"GET item {patch_item_id} failed ({resp.status_code}): {resp.text[:800]}")
        item_body = resp.json()
        updates, catalog_title = build_redump_values(
            session,
            base,
            creds,
            manifest,
            storage_uri_base=storage_uri_base,
            api=api,
            redump_cfg=dump_cfg,
            existing_item=item_body,
            wasabi_ingest_cfg=wasabi_ingest_cfg,
        )
        api.patch_item_rdf_values(session, base, creds, patch_item_id, updates, item_body=item_body)
        apply_item_resource_template(
            session,
            base,
            creds,
            patch_item_id,
            resource_template_id,
            resource_class_id=resource_class_id,
            api=api,
        )
        apply_item_item_sets(session, base, creds, patch_item_id, [item_set_id], api=api)
        set_resource_public(session, base, creds, "items", patch_item_id, is_public=is_public, api=api)
        item_id = patch_item_id
    else:
        rdf_values, catalog_title = build_redump_values(
            session,
            base,
            creds,
            manifest,
            storage_uri_base=storage_uri_base,
            api=api,
            redump_cfg=dump_cfg,
            wasabi_ingest_cfg=wasabi_ingest_cfg,
        )
        item_id = _post_catalog_item(
            session,
            base,
            creds,
            site_id=site_id,
            item_set_id=item_set_id,
            resource_template_id=resource_template_id,
            resource_class_id=resource_class_id,
            is_public=is_public,
            rdf_values=rdf_values,
            api=api,
        )

    return {
        "item_id": item_id,
        "omeka_item_id": item_id,
        "game_key": manifest.get("game_key"),
        "title": catalog_title,
        "site_slug": site_slug,
        "item_set": item_set_title,
        "resource_template_label": dump_cfg["resource_template_label"],
        "is_public": is_public,
        "patched": patch_item_id is not None,
        "catalog_only": True,
    }


def apply_dump_catalog(
    *,
    game_key: str,
    game_cfg_path: Path,
    handoff_path: Path | None = None,
    patch_item_id: int | None = None,
) -> dict[str, Any]:
    api = load_register_dip_omeka_api()
    apply_moby_metadata_to_item = api.apply_moby_metadata_to_item
    game_cfg = resolve_game_config(load_yaml(game_cfg_path), agent_version="apply-dump-catalog-omeka.py")
    if resolve_ingest_kind(game_cfg) != "dump":
        raise RuntimeError(f"ingest_kind must be dump for {game_cfg_path}")

    source = Path(game_cfg["source_game_folder"]).resolve()
    handoff = load_yaml(handoff_path) if handoff_path and handoff_path.is_file() else {}
    title_raw = str(game_cfg.get("omeka_item_title") or source.name)

    uploader_cfg = load_omeka_catalog_config(CONFIG_ROOT)
    dump_cfg = dump_catalog_config(uploader_cfg)

    ingest_cfg = load_ingest_config()
    db_cfg = load_yaml(Path(ingest_cfg["database"]["config_file"]))["postgres"]
    metadata_json = None
    inventory_json = None
    ledger_aip: str | None = None
    ledger_prefix: str | None = None
    ledger_sort_letter: str | None = None
    existing_item_id: int | None = patch_item_id

    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT omeka_item_id, metadata_json, inventory_json,
                       aip_wasabi_prefix, package_storage_prefix, status, sort_letter
                FROM game_ingest WHERE game_key = %s
                """,
                (game_key,),
            )
            row = cur.fetchone()
        if row:
            if row[5] and row[5] != "complete":
                return {"game_key": game_key, "skipped": True, "reason": f"ledger status {row[5]!r}"}
            if (
                not dump_cfg.get("ignore_ledger_omeka_item_id")
                and existing_item_id is None
                and row[0]
            ):
                existing_item_id = int(row[0])
            metadata_json = row[1]
            inventory_json = row[2]
            if isinstance(metadata_json, str):
                metadata_json = json.loads(metadata_json)
            if isinstance(inventory_json, str):
                inventory_json = json.loads(inventory_json)
            ledger_aip = row[3]
            ledger_prefix = row[4]
            ledger_sort_letter = row[6]

    manifest = manifest_from_sip_and_handoff(
        source_dir=source,
        game_key=game_key,
        omeka_title=title_raw,
        handoff=handoff,
        sort_letter=ledger_sort_letter,
    )

    api_cfg = require_omeka_api_config(uploader_cfg)
    cred_path = CONFIG_ROOT / api_cfg["credentials_file"]
    creds = load_yaml(cred_path)
    base = api_cfg["base_url"].rstrip("/")
    site_slug = api_cfg["site_slug"]
    item_set_title = dump_cfg["item_set_title"]
    is_public = dump_cfg["is_public"]
    storage_uri_base = dump_cfg["storage_uri_base"]

    session = requests.Session()
    site_id = api.resolve_site_id(session, base, creds, site_slug)
    resource_template_id, resource_class_id = resolve_resource_template(
        session, base, creds, dump_cfg["resource_template_label"], api=api
    )
    item_set_id = resolve_dump_item_set_id(
        session, base, creds, dump_cfg, is_public=is_public, api=api
    )
    moby_cfg = load_mobygames_config(CONFIG_ROOT)

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
        wasabi_ingest_cfg=ingest_cfg.get("wasabi"),
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
        apply_item_resource_template(
            session,
            base,
            creds,
            existing_item_id,
            resource_template_id,
            resource_class_id=resource_class_id,
            api=api,
        )
        apply_item_item_sets(session, base, creds, existing_item_id, [item_set_id], api=api)
        item_id = existing_item_id
    else:
        item_id = _post_catalog_item(
            session,
            base,
            creds,
            site_id=site_id,
            item_set_id=item_set_id,
            resource_template_id=resource_template_id,
            resource_class_id=resource_class_id,
            is_public=is_public,
            rdf_values=rdf_values,
            api=api,
        )

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
        "package_storage_prefix": ledger_prefix or manifest.get("package_storage_prefix"),
        "catalog_only": True,
    }


def _parse_item_id_flag(args: list[str]) -> tuple[list[str], int | None]:
    patch_item_id: int | None = None
    out: list[str] = []
    idx = 0
    while idx < len(args):
        if args[idx] == "--item-id" and idx + 1 < len(args):
            patch_item_id = int(args[idx + 1])
            idx += 2
        else:
            out.append(args[idx])
            idx += 1
    return out, patch_item_id


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage:\n"
            "  apply-dump-catalog-omeka.py <game.yaml> [handoff.yaml] [--item-id ID]\n"
            "  apply-dump-catalog-omeka.py --manifest <handoff.yaml> [--item-id ID]"
        )

    argv = sys.argv[1:]
    argv, patch_item_id = _parse_item_id_flag(argv)

    if argv and argv[0] in ("--manifest", "--handoff"):
        if len(argv) != 2:
            raise SystemExit("Usage: apply-dump-catalog-omeka.py --manifest <handoff.yaml> [--item-id ID]")
        manifest = load_yaml(Path(argv[1]))
        result = apply_handoff_dump_catalog(manifest, patch_item_id=patch_item_id)
        from yaml_config import dumps_json

        print(dumps_json(result, indent=2), end="")
        return

    game_cfg_path = Path(argv[0])
    handoff_path: Path | None = None
    for arg in argv[1:]:
        if arg.endswith((".yaml", ".yml")):
            handoff_path = Path(arg)
            break

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
    from yaml_config import dumps_json

    print(dumps_json(result, indent=2), end="")


if __name__ == "__main__":
    main()
