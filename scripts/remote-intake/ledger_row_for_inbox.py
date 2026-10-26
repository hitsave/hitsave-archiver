"""Match remote inbox package metadata to game_ingest rows."""
from __future__ import annotations

from ledger_lookup_slugs import build_ledger_lookup_slugs


def _fetch(cur, *, sql: str, params: tuple) -> tuple[str, int] | None:
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        return None
    return str(row[0]), int(row[1]) if row[1] is not None else None


def ledger_row_for_inbox_package(
    cur,
    *,
    ready_slug: str,
    package_folder_name: str | None,
    stored_lookup_slugs: list[str] | None,
) -> tuple[str | None, int | None]:
    lookup = list(stored_lookup_slugs or []) or build_ledger_lookup_slugs(
        ready_slug=ready_slug,
        package_folder_name=package_folder_name,
        files=None,
    )
    if ready_slug not in lookup:
        lookup = [ready_slug, *lookup]

    for slug in lookup:
        hit = _fetch(
            cur,
            sql="""
                SELECT status, omeka_item_id
                FROM game_ingest
                WHERE ingest_kind = 'dump'
                  AND (
                    game_key = %s
                    OR source_path LIKE %s
                    OR source_path LIKE %s
                  )
                ORDER BY (status = 'complete') DESC, updated_at DESC
                LIMIT 1
                """,
            params=(slug, f"%/{slug}", f"%/{slug}/%"),
        )
        if hit:
            return hit

    for slug in lookup:
        bare = slug[7:] if slug.startswith("redump-") else slug
        if not bare or bare == slug:
            continue
        hit = _fetch(
            cur,
            sql="""
                SELECT status, omeka_item_id
                FROM game_ingest
                WHERE ingest_kind = 'dump'
                  AND game_key LIKE %s
                ORDER BY (status = 'complete') DESC, updated_at DESC
                LIMIT 1
                """,
            params=(f"{bare}-%",),
        )
        if hit:
            return hit

    return None, None
