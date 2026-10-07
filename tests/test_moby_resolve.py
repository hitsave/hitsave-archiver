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


def test_attribution_appends_suffix_when_sentence_does_not_name_mobygames() -> None:
    assert moby_attribution_text(None, {"attribution_suffix": "Data by MobyGames.com"}) == (
        "Data by MobyGames.com"
    )
