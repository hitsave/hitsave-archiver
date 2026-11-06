"""Ledger filters: status/flag chips are links, and the ledger page says what it's filtered to."""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import re

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))
sys.path.insert(0, str(ROOT / "scripts"))

from filters import filter_banner, flag_chip, parse_filters, status_chip  # noqa: E402


def _href(html_str: str) -> dict[str, list[str]]:
    href = re.search(r'href="([^"]*)"', html_str).group(1).replace("&amp;", "&")
    assert urlparse(href).path == "/"
    return parse_qs(urlparse(href).query)


def test_parse_filters_drops_empty_and_unknown() -> None:
    assert parse_filters({"flag": ["bogus"], "status": [""]}) == {}
    assert parse_filters({"status": [" complete "], "flag": ["no_omeka"]}) == {
        "status": "complete",
        "flag": "no_omeka",
    }


def test_status_chip_links_and_escapes() -> None:
    chip = status_chip("<odd> status", 3, active=False)
    assert _href(chip) == {"status": ["<odd> status"]}
    assert "&lt;odd&gt; status" in chip
    assert "<strong>3</strong>" in chip
    assert "chip-active" not in chip
    assert "chip-active" in status_chip("complete", 1, active=True)


def test_flag_chip_links_to_flag_with_label() -> None:
    chip = flag_chip("no_omeka", 7)
    assert _href(chip) == {"flag": ["no_omeka"]}
    assert "complete, no catalog record" in chip
    assert "chip-warn" in chip


def test_banner_names_filter_and_clears_to_root() -> None:
    assert filter_banner({}) == ""
    banner = filter_banner({"status": "failed", "flag": "attention"})
    assert "failed" in banner
    assert "needs attention" in banner
    assert 'href="/"' in banner
    assert "Clear filter" in banner
