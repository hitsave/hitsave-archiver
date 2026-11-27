"""Attach a new bag to an Omeka item that already exists (game YAML `attach_to_omeka_item`).

Used for press kits that were catalogued by hand before the bag pipeline: the item keeps its
title, description and other values. Registration adds the bag as a DIP package media with
the item's own visibility, then hides the item's older flat file media (they stay on the item,
private, so nothing is lost and they can be shown again).
"""
from __future__ import annotations

from typing import Any, Callable

from omeka_dip_media import media_is_dip_package

ApiUrl = Callable[[str, str, dict], str]
JSON_LD = {"Accept": "application/ld+json"}


def attach_item_id(game_cfg: dict) -> int | None:
    """The existing item id from game YAML, or None when the key is absent."""
    raw = game_cfg.get("attach_to_omeka_item")
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise SystemExit(f"attach_to_omeka_item must be an Omeka item id, got {raw!r}")
    try:
        item_id = int(raw)
    except (TypeError, ValueError):
        raise SystemExit(f"attach_to_omeka_item must be an Omeka item id, got {raw!r}") from None
    if item_id <= 0:
        raise SystemExit(f"attach_to_omeka_item must be a positive item id, got {raw!r}")
    return item_id


def get_item(session: Any, base: str, creds: dict, item_id: int, *, api_url: ApiUrl) -> dict:
    resp = session.get(api_url(base, f"items/{item_id}", creds), headers=JSON_LD, timeout=120)
    if not resp.ok:
        raise SystemExit(
            f"attach_to_omeka_item {item_id}: GET item failed ({resp.status_code}); "
            "check the id before rerunning"
        )
    return resp.json()


def item_media(session: Any, base: str, creds: dict, item_body: dict, *, api_url: ApiUrl) -> list[dict]:
    """Full media bodies for every media on the item, in item order."""
    out = []
    for ref in item_body.get("o:media") or []:
        mid = ref.get("o:id")
        if mid is None:
            continue
        resp = session.get(api_url(base, f"media/{int(mid)}", creds), headers=JSON_LD, timeout=120)
        if not resp.ok:
            raise RuntimeError(f"GET media {mid} failed ({resp.status_code}): {resp.text[:400]}")
        out.append(resp.json())
    return out


def existing_package_id(media: list[dict], *, dip_ingester: str) -> int | None:
    for body in media:
        if media_is_dip_package(body, dip_ingester=dip_ingester):
            return int(body["o:id"])
    return None


def check_existing_package(package_id: int | None, ledger_media_id: int | None, item_id: int) -> bool:
    """True when the item already holds this ledger row's bag (a rerun).

    A package media the ledger doesn't know about belongs to another bag: stop rather than
    repoint it or add a second package.
    """
    if package_id is None:
        return False
    if ledger_media_id is not None and int(ledger_media_id) == package_id:
        return True
    raise SystemExit(
        f"Item {item_id} already has a bag (media {package_id}) that this ledger row didn't attach; "
        "not adding a second one"
    )


def flat_media_to_hide(media: list[dict], *, keep_media_id: int, dip_ingester: str) -> list[dict]:
    """Public media other than the bag (and any other package media) — the old flat files."""
    return [
        body
        for body in media
        if int(body["o:id"]) != keep_media_id
        and not media_is_dip_package(body, dip_ingester=dip_ingester)
        and str(body.get("o:ingester") or "") != dip_ingester
        and body.get("o:is_public") is True
    ]


def hide_media(session: Any, base: str, creds: dict, media_id: int, *, api_url: ApiUrl) -> None:
    """Make one media private. A body with only `o:` keys leaves the media's values alone."""
    resp = session.patch(
        api_url(base, f"media/{media_id}", creds),
        json={"o:is_public": False},
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"PATCH media {media_id} failed ({resp.status_code}): {resp.text[:400]}")
