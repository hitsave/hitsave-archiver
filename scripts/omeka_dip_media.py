"""Detect Omeka S DIP package media (not stray image/file attachments)."""
from __future__ import annotations

from typing import Any, Callable


def media_is_dip_package(media_body: dict[str, Any], *, dip_ingester: str) -> bool:
    """True when media is omeka_dip_package or Wasabi S3 BagIt DIP."""
    renderer = str(media_body.get("o:renderer") or media_body.get("o:ingester") or "").strip()
    if renderer == dip_ingester:
        return True
    data = media_body.get("data") if isinstance(media_body.get("data"), dict) else {}
    if str(data.get("package_storage_prefix") or "").strip() and data.get("dip_storage") == "wasabi-s3":
        return True
    if str(data.get("dip_wasabi_uri") or "").startswith("s3://") and renderer == dip_ingester:
        return True
    return False


def find_dip_media_id_on_item(
    session: Any,
    base: str,
    creds: dict,
    item_id: int,
    dip_ingester: str,
    *,
    api_url: Callable[[str, str, dict], str],
) -> int | None:
    url = api_url(base, f"items/{item_id}", creds)
    resp = session.get(url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        return None
    body = resp.json()
    for ref in body.get("o:media") or []:
        mid = ref.get("o:id")
        if mid is None:
            continue
        media_id = int(mid)
        get_media = api_url(base, f"media/{media_id}", creds)
        mresp = session.get(get_media, headers={"Accept": "application/ld+json"}, timeout=120)
        if not mresp.ok:
            continue
        if media_is_dip_package(mresp.json(), dip_ingester=dip_ingester):
            return media_id
    return None
