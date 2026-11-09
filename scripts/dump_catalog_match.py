#!/usr/bin/env python3
"""Find catalog items that already describe a dump's Redump disc, before creating a new one.

Many discs were catalogued by hand before anyone dumped them (photos, a description, a link to the
redump.info disc page). The duplicate-dump guard in ingest-folder.py can't see those: it compares
disc images, and a hand-made record has none. When a new dump's Redump disc page is already linked
from an item, registration holds the dump for review instead of creating a second item.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

# Set to 1 to create the new item even though another item links the same disc.
ALLOW_ENV = "HITSAVE_ALLOW_NEW_CATALOG_ITEM"
HOLD_REASON = "catalog_match"  # ledger block_reason for a held dump

_DISC_URL = re.compile(r"redump\.(?:info|org)/disc/(\d+)")
_SITES = ("redump.info", "redump.org")
_PER_PAGE = 50


def redump_disc_id(rdf_values: Mapping[str, list]) -> str | None:
    """The disc number from the item's redump.info link (dcterms:relation), if registration found one."""
    for row in rdf_values.get("dcterms:relation") or []:
        found = _DISC_URL.search(str(row.get("@id") or ""))
        if found:
            return found.group(1)
    return None


def _links_disc(item: Mapping[str, Any], disc_id: str) -> bool:
    exact = re.compile(rf"redump\.(?:info|org)/disc/{re.escape(disc_id)}(?!\d)")
    for key, rows in item.items():
        if key.startswith("o:") or not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, Mapping) and exact.search(f"{row.get('@value') or ''} {row.get('@id') or ''}"):
                return True
    return False


def find_catalog_matches(session, base: str, creds: dict, disc_id: str, *, api) -> list[tuple[int, str]]:
    """Items (id, title) with a value linking redump.info or redump.org disc ``disc_id``, oldest first.

    Searches every property, so a link in a description's text counts as well as a link field.
    With API credentials the search includes private items.
    """
    found: dict[int, str] = {}
    for site in _SITES:
        page = 1
        while True:
            resp = session.get(
                api.api_url(base, "items", creds),
                params={
                    "property[0][type]": "in",
                    "property[0][text]": f"{site}/disc/{disc_id}",
                    "per_page": _PER_PAGE,
                    "page": page,
                },
                timeout=120,
            )
            resp.raise_for_status()
            items = resp.json()
            for item in items:
                if _links_disc(item, disc_id):
                    found.setdefault(int(item["o:id"]), str(item.get("o:title") or ""))
            if len(items) < _PER_PAGE:
                break
            page += 1
    return sorted(found.items())


def matches_for_new_item(
    session,
    base: str,
    creds: dict,
    rdf_values: Mapping[str, list],
    *,
    existing_item_id: int | None,
    api,
    env: Mapping[str, str] = os.environ,
) -> list[tuple[int, str]]:
    """Items that make creating this dump's item a likely duplicate; [] when it's fine to go ahead.

    Only a new item is checked: re-applying to the dump's own item never holds it.
    """
    if existing_item_id is not None or env.get(ALLOW_ENV) == "1":
        return []
    disc_id = redump_disc_id(rdf_values)
    if disc_id is None:
        return []
    return find_catalog_matches(session, base, creds, disc_id, api=api)


def hold_note(disc_id: str, matches: list[tuple[int, str]]) -> str:
    """The ledger error_message for a held dump."""
    items = ", ".join(f"item {item_id} ({title})" for item_id, title in matches)
    verb = "describes" if len(matches) == 1 else "describe"
    return (
        f"Not registered in Omeka: {items} already {verb} redump.info disc {disc_id}. "
        f"Review it: merge the dump into the existing item by hand, or create a new item with "
        f"{ALLOW_ENV}=1 register-dump-omeka.sh --game-key <key>."
    )
