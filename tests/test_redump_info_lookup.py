#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from redump_info_lookup import parse_disc_page_titles  # noqa: E402


def test_parse_disc_page_titles_fwd0307_fixture() -> None:
    html = (ROOT / "fixtures" / "redump-disc-fwd0307-snippet.html").read_text()
    parsed = parse_disc_page_titles(html)
    assert parsed is not None
    assert parsed.title == "Famitsu Wave DVD 2003 7-gatsugou Tokubetsu Furoku"
    assert parsed.foreign_title == "ファミ通Ｗａｖｅ　ＤＶＤ　２００３　７月号　特別付録"
    assert parsed.alternative_titles == ("Famitsu Wave DVD July 2003",)
    assert parsed.contents is None


def test_parse_disc_page_contents_fixture() -> None:
    html = (ROOT / "fixtures" / "redump-disc-81693-contents-snippet.html").read_text()
    parsed = parse_disc_page_titles(html)
    assert parsed is not None
    assert parsed.contents is not None
    assert "Bistro Cupid 2" in parsed.contents
    assert "Zelda no Densetsu" in parsed.contents
    assert "&" in parsed.contents and "&amp;" not in parsed.contents
