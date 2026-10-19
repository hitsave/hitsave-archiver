#!/usr/bin/env python3
"""Unit tests for MobyGames → Omeka attribution strings."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

MOBY_CFG = {
    "attribution_suffix": "Data by MobyGames.com",
    "public_game_url_template": "https://www.mobygames.com/game/{game_id}",
}

from moby_resolve import (  # noqa: E402
    MobyResult,
    apply_moby_omeka_profile_to_metadata,
    build_moby_attribution_sentence,
    moby_attribution_text,
    moby_attribution_for_omeka_rights,
    moby_description_for_omeka,
    moby_search_title_candidates,
    moby_title_matches_search,
    pick_moby_search_game,
    resolve_web_search_fallback,
    web_search_fallback_enabled,
    web_search_fallback_max_results,
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


def test_moby_title_matches_search_rejects_akuma_demon_spawn() -> None:
    assert not moby_title_matches_search("Akuma: Demon Spawn", "Akuma de Oshioki")
    assert moby_title_matches_search("Akuma de Oshioki! Marukido", "Akuma de Oshioki")


def test_pick_moby_search_game_unique_title_token_match() -> None:
    games = [
        {"game_id": 1, "title": "Alien³"},
        {"game_id": 2, "title": "Alien Breed: Impact"},
    ]
    chosen = pick_moby_search_game("Alien Breed Impact", games)
    assert chosen is not None
    assert chosen["game_id"] == 2


def test_web_search_fallback_config_object() -> None:
    cfg = {"web_search_fallback": {"enabled": True, "max_results": 20}}
    assert web_search_fallback_enabled(cfg) is True
    assert web_search_fallback_max_results(cfg) == 20
    assert web_search_fallback_enabled({"web_search_fallback": False}) is False


def test_resolve_web_search_fallback_skips_first_bad_ddg_hit(monkeypatch) -> None:
    class FakeClient:
        def resolve_by_id(self, game_id: int, search_title: str = "", **kwargs: object) -> MobyResult:
            titles = {
                2865: "Akuma: Demon Spawn",
                9001: "Akuma de Oshioki! Marukido Sadoshiki",
            }
            title = titles.get(game_id, "Unknown")
            return MobyResult(
                "ok",
                None,
                game_id,
                title,
                {"moby_game_id": game_id, "moby_title": title},
                "first_release",
            )

    monkeypatch.setattr(
        "moby_web_search.duckduckgo_moby_game_candidates",
        lambda *args, **kwargs: [
            (2865, "https://www.mobygames.com/game/2865/"),
            (9001, "https://www.mobygames.com/game/9001/"),
        ],
    )
    moby_cfg = {
        "public_game_url_template": "https://www.mobygames.com/game/{game_id}",
        "web_search_fallback": {"enabled": True, "max_results": 10},
    }
    result = resolve_web_search_fallback(FakeClient(), "Akuma de Oshioki", moby_cfg)
    assert result.status == "ok"
    assert result.moby_game_id == 9001
    assert result.metadata_json is not None
    assert result.metadata_json.get("moby_match_source") == "duckduckgo"
    assert result.metadata_json.get("moby_ddg_candidates_tried") == 2


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
    text = moby_description_for_omeka(meta, MOBY_CFG)
    assert text.startswith("Official description (ad blurb) (PlayStation Store Description)")
    assert "Step into the shoes" in text


def test_attribution_for_omeka_rights_press_omits_release_date() -> None:
    meta = {
        "moby_catalog_fields": ["publisher", "developer", "first_release_date"],
    }
    text = moby_attribution_for_omeka_rights(meta, MOBY_CFG, omeka_profile="press_material")
    assert "Release date" not in text
    assert "Publisher" in text


def test_apply_omeka_profile_press_strips_release_from_catalog_fields() -> None:
    meta = {
        "first_release_date": "2017-06-02",
        "publishers": ["Bandai Namco"],
        "moby_catalog_fields": ["publisher", "first_release_date"],
    }
    apply_moby_omeka_profile_to_metadata(meta, "press_material", MOBY_CFG)
    assert "first_release_date" not in meta["moby_catalog_fields"]
    assert "Release date" not in meta["attribution"]


def test_attribution_for_omeka_rights_omits_description_fields() -> None:
    meta = {
        "description_plain": "A game about heroes.",
        "moby_catalog_fields": ["description", "publisher"],
    }
    text = moby_attribution_for_omeka_rights(meta, MOBY_CFG)
    assert "Description" not in text
    assert "Publisher" in text


def test_attribution_appends_suffix_when_sentence_does_not_name_mobygames() -> None:
    assert moby_attribution_text(None, {"attribution_suffix": "Data by MobyGames.com"}) == (
        "Data by MobyGames.com"
    )
