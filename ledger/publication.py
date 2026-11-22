"""Record the first time an Omeka item is published (schema/015_publication.sql).

The Discord digest (scripts/discord_digest.py) announces these rows. A failure here must never
break the publish itself: the digest's reconcile step finds the item at its next run.
"""
from __future__ import annotations

from typing import Callable

SQL = (
    "INSERT INTO publication (omeka_item_id, game_ingest_id, published_by, source) "
    "SELECT %s, (SELECT id FROM game_ingest WHERE omeka_item_id = %s ORDER BY id LIMIT 1), %s, %s "
    "ON CONFLICT (omeka_item_id) DO NOTHING"
)


def record_published(connect: Callable, omeka_item_id: int, user: str, source: str = "ledger") -> bool:
    """True when a new row was added; False when the item was already recorded or on any error."""
    try:
        with connect() as conn:
            cur = conn.execute(SQL, (omeka_item_id, omeka_item_id, user, source))
            return cur.rowcount == 1
    except Exception as exc:  # noqa: BLE001 - the publish already succeeded; reconcile catches up
        print(f"publication: couldn't record item {omeka_item_id}: {type(exc).__name__}", flush=True)
        return False
