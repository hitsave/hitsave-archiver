"""Ledger helpers for package_id / sip_fingerprint."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from package_identity import PackageIdentity, build_package_identity
from preservation_common import (
    bagit_package_s3_uri_for_prefix,
    resolve_wasabi_bucket,
    wasabi_bucket_for_storage_prefix,
)
from submission_sources import resolve_submission_source


def lookup_by_sip_fingerprint(conn, sip_fingerprint: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT game_key, package_id, package_uuid, title_slug, sort_letter
            FROM game_ingest
            WHERE sip_fingerprint = %s
            """,
            (sip_fingerprint,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "game_key": row[0],
        "package_id": row[1] or row[0],
        "package_uuid": row[2],
        "title_slug": row[3],
        "sort_letter": row[4],
    }


def resolve_package_identity(
    conn,
    *,
    source: Path,
    material_root: Path,
    omeka_item_title: str,
    press_material_root: Path | None = None,
) -> PackageIdentity:
    _ = press_material_root  # legacy callers; use material_root
    submission = resolve_submission_source(source)
    if not submission or not submission.get("id"):
        raise RuntimeError(
            f"No submission-sources.yaml match for SIP {source}; cannot assign package_id"
        )
    submission_id = str(submission["id"])
    draft = build_package_identity(
        source=source,
        press_material_root=material_root,
        submission_source_id=submission_id,
        omeka_item_title=omeka_item_title,
    )
    existing = lookup_by_sip_fingerprint(conn, draft.sip_fingerprint)
    if existing and existing.get("package_id"):
        return build_package_identity(
            source=source,
            press_material_root=material_root,
            submission_source_id=submission_id,
            omeka_item_title=omeka_item_title,
            existing_package_id=str(existing["package_id"]),
            existing_package_uuid=str(existing["package_uuid"]) if existing.get("package_uuid") else None,
        )
    return draft


def ledger_bagit_wasabi_paths(
    db_cfg: dict,
    game_key: str,
    wasabi_ingest_cfg: dict,
) -> tuple[str, str]:
    """Return (s3_uri, package_storage_prefix) for a BagIt package row."""
    import psycopg

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
                SELECT package_storage_prefix, package_id, sort_letter, game_key, ingest_kind,
                       source_path
                FROM game_ingest WHERE game_key = %s
                """,
                (game_key,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise RuntimeError(f"No ledger row for game_key={game_key!r}")
    storage_prefix, package_id, sort_letter, ledger_game_key, ingest_kind, source_path = row
    kind = str(ingest_kind or "").strip() or None
    if storage_prefix:
        prefix = str(storage_prefix).strip("/")
        bucket = wasabi_bucket_for_storage_prefix(
            wasabi_ingest_cfg, prefix, ingest_kind=kind
        )
    else:
        if not kind:
            raise RuntimeError(
                f"Cannot resolve Wasabi bucket for {ledger_game_key!r}: "
                "missing package_storage_prefix and ingest_kind"
            )
        bucket = resolve_wasabi_bucket(wasabi_ingest_cfg, kind)
    if storage_prefix:
        prefix = str(storage_prefix).strip("/")
        return bagit_package_s3_uri_for_prefix(bucket, prefix), prefix

    from package_identity import relative_sip_path
    from preservation_common import (
        load_ingest_config,
        material_root_for_kind,
        wasabi_package_storage_prefix,
    )

    if not kind:
        raise RuntimeError(
            f"Cannot build Wasabi prefix for {ledger_game_key!r}: ingest_kind is required"
        )
    if not source_path:
        raise RuntimeError(
            f"Cannot build Wasabi prefix for {ledger_game_key!r}: source_path is required"
        )
    paths = load_ingest_config().get("paths") or {}
    rel = relative_sip_path(
        Path(str(source_path)),
        material_root_for_kind(kind, paths),
    )
    pid = package_id or ledger_game_key
    letter = sort_letter or "X"
    prefix = wasabi_package_storage_prefix(
        kind,
        package_id=str(pid),
        sort_letter=str(letter),
        relative_sip_path=rel,
    )
    return bagit_package_s3_uri_for_prefix(bucket, prefix), prefix
