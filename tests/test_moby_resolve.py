#!/usr/bin/env python3
"""Unit tests for MobyGames → Omeka attribution strings."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from moby_resolve import (  # noqa: E402
    build_moby_attribution_sentence,
    moby_attribution_text,
    moby_description_for_omeka,
    moby_search_title_candidates,
)


def test_attribution_lists_fields_with_information_from_mobygames() -> None:
    meta = {
        "moby_catalog_fields": ["publisher", "developer", "first_release_date"],
    }
    assert (
        build_moby_attribution_sentence(meta)
        == "Publisher, Developer, and Release date information from MobyGames."
    )


def test_attribution_title_cases_single_field() -> None:
    meta = {"moby_catalog_fields": ["publisher"]}
    assert build_moby_attribution_sentence(meta) == "Publisher information from MobyGames."


def test_attribution_empty_fields_fallback() -> None:
    assert build_moby_attribution_sentence({}) == "Catalog information from MobyGames."


def test_attribution_skips_redundant_data_suffix_when_mobygames_named() -> None:
    meta = {"moby_game_id": 1, "moby_catalog_fields": ["publisher"]}
    moby_cfg = {"attribution_suffix": "Data by MobyGames.com"}
    assert moby_attribution_text(meta, moby_cfg) == "Publisher information from MobyGames."


def test_search_title_candidates_champion_to_championship() -> None:
    alts = moby_search_title_candidates("PAC-MAN Champion Edition DX")
    assert "PAC-MAN Champion Edition DX" in alts
    assert "PAC-MAN Championship Edition DX" in alts
    assert any(a.startswith("Pac-") for a in alts)


def test_description_for_omeka_labels_ad_blurb() -> None:
    meta = {
        "moby_game_id": 1,
        "description_source": "ad_blurb",
        "official_ad_blurb_plain": "Step into the shoes of one of two main characters.",
        "official_ad_blurb_source": "PlayStation Store Description",
        "moby_catalog_fields": ["official_description", "publisher"],
    }
    text = moby_description_for_omeka(meta, {})
    assert text.startswith("Official description (ad blurb) (PlayStation Store Description)")
    assert "Step into the shoes" in text


def test_attribution_appends_suffix_when_sentence_does_not_name_mobygames() -> None:
    assert moby_attribution_text(None, {"attribution_suffix": "Data by MobyGames.com"}) == (
        "Data by MobyGames.com"
    )
