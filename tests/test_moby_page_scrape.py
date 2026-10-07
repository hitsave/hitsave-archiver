#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from moby_page_scrape import parse_moby_description_html  # noqa: E402


def test_parse_description_section_prefers_longest_ad_blurb() -> None:
    html = (ROOT / "fixtures" / "moby-game-page-ad-blurb.html").read_text()
    text, source = parse_moby_description_html(html)
    assert "Step into the shoes" in text
    assert "PlayStation Store Description" in source
    assert "Short box text" not in text


def test_parse_cloudflare_challenge_returns_empty() -> None:
    text, source = parse_moby_description_html("<html>Just a moment...</html>")
    assert text == ""
    assert source == ""
