#!/usr/bin/env python3
"""Fetch MobyGames game pages (curl) and parse Description / Ad Blurb HTML."""
from __future__ import annotations

import html as html_module
import re
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlparse

_AD_BLURBS_SUFFIX = re.compile(r"[ \t]*\(\s*from\s+Ad\s+Blurbs\s*\)", re.I)
_CF_CHALLENGE_MARKERS = ("just a moment", "cf-chl", "challenge-platform", "enable javascript and cookies")
_SECTION_RE = re.compile(
    r"<h2[^>]*>\s*(Description|Ad\s+Blurbs?)\s*</h2>(.*?)(?=<h2\b|class=\"sideBarLinks\"|$)",
    re.I | re.S,
)
_HEADING_RE = re.compile(r"<h[34][^>]*>(.*?)</h[34]>", re.I | re.S)


def html_to_plain(fragment: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", fragment or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def parse_moby_description_html(html: str) -> tuple[str, str]:
    """Return (plain_text, source_label) from a Moby game or ad-blurbs page."""
    if not html or any(m in html.casefold() for m in _CF_CHALLENGE_MARKERS):
        return "", ""
    best = ""
    best_source = "MobyGames"
    for match in _SECTION_RE.finditer(html):
        block = match.group(2)
        source = "MobyGames"
        heading = _HEADING_RE.search(block)
        body = block
        if heading:
            source = _AD_BLURBS_SUFFIX.sub("", html_to_plain(heading.group(1))).strip() or source
            body = block[heading.end() :]
        text = html_to_plain(body)
        if len(text) > len(best):
            best, best_source = text, source
    return best, best_source


def curl_fetch_url(url: str, *, cookie_jar: Path | None = None, timeout_sec: int = 45) -> str:
    cmd = [
        "curl",
        "-sS",
        "-L",
        "--max-time",
        str(timeout_sec),
        "-A",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "-H",
        "Accept: text/html,application/xhtml+xml",
        url,
    ]
    if cookie_jar and cookie_jar.is_file():
        cmd.extend(["-b", str(cookie_jar)])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def game_page_urls(moby_url: str) -> list[str]:
    base = moby_url.rstrip("/") + "/"
    if not urlparse(base).scheme:
        base = "https://" + base.lstrip("/")
    return [base, urljoin(base, "ad-blurbs/")]


def fetch_ad_blurb_from_web(
    moby_url: str,
    *,
    cookie_jar: Path | None = None,
    min_chars: int = 80,
) -> tuple[str, str]:
    best = ""
    best_source = ""
    for url in game_page_urls(moby_url):
        html = curl_fetch_url(url, cookie_jar=cookie_jar)
        text, source = parse_moby_description_html(html)
        if len(text) >= min_chars and len(text) > len(best):
            best, best_source = text, source
    return best, best_source
