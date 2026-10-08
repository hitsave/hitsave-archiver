#!/usr/bin/env python3
"""Resolve Redump disc IDs and catalog titles via redump.info (never redump.org)."""
from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

_DISC_ID_IN_PATH = re.compile(r"/disc/(\d+)/?$")
_DISC_HREF = re.compile(r'href="/disc/(\d+)/?"')
_SHA1_HEX = re.compile(r"^[0-9a-f]{40}$", re.I)
_TITLE_BOX = re.compile(r'<div class="disc-title-box">(.*?)</div>', re.DOTALL | re.I)
_H2 = re.compile(r"<h2([^>]*)>([^<]+)</h2>", re.I)
_ALT_TITLE = re.compile(r"<b>Alternative Title</b>\s*:\s*([^\n<]+)", re.I)
_DISC_CONTENTS = re.compile(r'<p class="pre-wrap disc-contents">(.*?)</p>', re.DOTALL | re.I)


@dataclass(frozen=True)
class RedumpDiscTitles:
    """Catalog metadata scraped from a redump.info disc page."""

    title: str
    foreign_title: str | None
    alternative_titles: tuple[str, ...]
    contents: str | None


def normalize_sha1(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower().replace(" ", "")
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    return cleaned if _SHA1_HEX.match(cleaned) else None


def _unique_disc_ids(html: str) -> list[int]:
    ordered: list[int] = []
    seen: set[int] = set()
    for match in _DISC_HREF.finditer(html):
        disc_id = int(match.group(1))
        if disc_id not in seen:
            seen.add(disc_id)
            ordered.append(disc_id)
    return ordered


def _disc_id_from_url(url: str) -> int | None:
    path = urlparse(url).path
    match = _DISC_ID_IN_PATH.search(path)
    return int(match.group(1)) if match else None


def _request_headers(redump_cfg: dict[str, Any]) -> dict[str, str]:
    return {"User-Agent": str(redump_cfg.get("user_agent") or "HitSaveIntake/1.0")}


def _pre_wrap_paragraph_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw)
    return html_module.unescape(text).strip()


def parse_disc_page_titles(page_html: str) -> RedumpDiscTitles | None:
    """Parse title, alternates, and Contents box text from a disc page."""
    box = _TITLE_BOX.search(page_html)
    if not box:
        return None

    main_title: str | None = None
    foreign_title: str | None = None
    for match in _H2.finditer(box.group(1)):
        attrs, raw = match.group(1), match.group(2)
        text = html_module.unescape(raw.strip())
        if not text:
            continue
        if "foreign-title" in attrs.lower():
            foreign_title = text
        elif main_title is None:
            main_title = text

    if not main_title:
        return None

    alts: list[str] = []
    for match in _ALT_TITLE.finditer(page_html):
        val = html_module.unescape(match.group(1).strip())
        if val:
            alts.append(val)

    contents: str | None = None
    contents_match = _DISC_CONTENTS.search(page_html)
    if contents_match:
        contents = _pre_wrap_paragraph_text(contents_match.group(1)) or None

    return RedumpDiscTitles(
        title=main_title,
        foreign_title=foreign_title,
        alternative_titles=tuple(alts),
        contents=contents,
    )


def fetch_redump_disc_titles(
    session: requests.Session,
    disc_id: int,
    redump_cfg: dict[str, Any],
) -> RedumpDiscTitles | None:
    timeout = int(redump_cfg.get("lookup_timeout_seconds") or 30)
    url = redump_disc_page_url(disc_id, redump_cfg)
    resp = session.get(url, headers=_request_headers(redump_cfg), timeout=timeout)
    resp.raise_for_status()
    return parse_disc_page_titles(resp.text)


def resolve_redump_disc_id(
    session: requests.Session,
    *,
    sha1: str | None,
    redump_cfg: dict[str, Any],
) -> int | None:
    """
    Find a redump.info disc id by SHA-1 (GET /discs?q={sha1}).

    Hash search avoids serial/title collisions. Uses only redump.info.
    Returns None when SHA-1 is missing or search is ambiguous.
    """
    digest = normalize_sha1(sha1)
    if not digest:
        return None

    resolved = resolve_redump_disc_by_sha1(session, sha1=digest, redump_cfg=redump_cfg)
    return resolved[0] if resolved else None


def resolve_redump_disc_by_sha1(
    session: requests.Session,
    *,
    sha1: str | None,
    redump_cfg: dict[str, Any],
) -> tuple[int, RedumpDiscTitles] | None:
    """
    Resolve disc id by SHA-1 and return catalog titles from the disc page.

    Uses GET /discs?q={sha1}. Returns None when SHA-1 is missing or search is ambiguous.
    """
    digest = normalize_sha1(sha1)
    if not digest:
        return None

    site = str(redump_cfg.get("site_url") or "https://redump.info").rstrip("/")
    timeout = int(redump_cfg.get("lookup_timeout_seconds") or 30)
    headers = _request_headers(redump_cfg)

    resp = session.get(
        f"{site}/discs",
        params={"q": digest},
        headers=headers,
        timeout=timeout,
        allow_redirects=True,
    )
    resp.raise_for_status()

    page_html: str | None = None
    disc_id: int | None = None

    from_url = _disc_id_from_url(resp.url)
    if from_url is not None:
        disc_id = from_url
        page_html = resp.text
    else:
        ids = _unique_disc_ids(resp.text)
        if len(ids) == 1:
            disc_id = ids[0]

    if disc_id is None:
        return None

    titles = parse_disc_page_titles(page_html) if page_html else None
    if titles is None:
        titles = fetch_redump_disc_titles(session, disc_id, redump_cfg)
    if titles is None:
        return None

    return disc_id, titles


def redump_disc_page_url(disc_id: int, redump_cfg: dict[str, Any]) -> str:
    site = str(redump_cfg.get("site_url") or "https://redump.info").rstrip("/")
    tpl = str(redump_cfg.get("disc_url") or "{site}/disc/{disc_id}")
    return tpl.format(site=site, disc_id=disc_id)
