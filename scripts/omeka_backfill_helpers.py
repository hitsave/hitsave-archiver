"""Shared Omeka REST helpers for ledger backfill / dedupe scripts."""
from __future__ import annotations

import re

import requests

from normalize_display_title import TITLE_SUFFIX_SEP, normalize_display_title


def api_url(base: str, path: str, creds: dict) -> str:
    from urllib.parse import urlencode

    q = urlencode(
        {
            "key_identity": creds["key_identity"],
            "key_credential": creds["key_credential"],
        }
    )
    return f"{base.rstrip('/')}/{path.lstrip('/')}?{q}"


def title_match_key(title: str) -> str:
    text = normalize_display_title(title)
    if TITLE_SUFFIX_SEP in text:
        text = text.split(TITLE_SUFFIX_SEP, 1)[0].strip()
    text = re.sub(r"\s+", " ", text).casefold()
    return text


def fetch_omeka_items(session: requests.Session, base: str, creds: dict) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        url = api_url(base, "items", creds)
        resp = session.get(url, params={"page": page, "per_page": 100}, timeout=120)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out


def item_title(item: dict) -> str:
    rows = item.get("dcterms:title") or []
    for row in rows:
        val = (row.get("@value") or "").strip()
        if val:
            return val
    return ""


def primary_media_id(item: dict) -> int | None:
    media = item.get("o:media") or []
    if not media:
        return None
    return int(media[0]["o:id"])
