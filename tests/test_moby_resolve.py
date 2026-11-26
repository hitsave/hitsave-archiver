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
    moby_title_key,
    moby_title_matches_search,
    pick_moby_search_game,
    resolve_web_search_fallback,
    resolve_word_search_fallback,
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




# --- API word-search fallback (web search from the host returns junk) ---

JETZ_SEARCH = [
    {"game_id": 6453, "title": "WarJetz"},
    {"game_id": 17944, "title": "King Size: WarJetz + Army Men Sarge's Heroes 2"},
    {"game_id": 43459, "title": "Hot Wheels: Jetz"},
]


class WordSearchClient:
    """Fake MobyClient: search_titles answers per word, resolve_by_id echoes the title."""

    def __init__(self, by_query: dict[str, list[dict]]):
        self.by_query = by_query
        self.queries: list[str] = []

    def search_titles(self, title: str) -> list[dict]:
        self.queries.append(title)
        return self.by_query.get(title.casefold(), [])

    def resolve_by_id(self, game_id: int, search_title: str = "", **kwargs: object) -> MobyResult:
        title = str(kwargs.get("fallback_title") or "")
        return MobyResult(
            "ok", None, game_id, title, {"moby_game_id": game_id, "moby_title": title}, "first_release"
        )


def test_moby_title_key_ignores_punctuation_and_case() -> None:
    assert moby_title_key("Hot Wheels: Jetz") == moby_title_key("hot wheels jetz")
    assert moby_title_key("Hot Wheels: Jetz") != moby_title_key("WarJetz")


def test_word_search_fallback_finds_title_that_needs_a_colon() -> None:
    client = WordSearchClient({"jetz": JETZ_SEARCH})
    result = resolve_word_search_fallback(client, "Hot Wheels Jetz")
    assert result.status == "ok"
    assert result.moby_game_id == 43459
    assert result.metadata_json is not None
    assert result.metadata_json.get("moby_match_source") == "api_word_search"


def test_word_search_fallback_is_ambiguous_when_several_titles_match_exactly() -> None:
    lemmings = [
        {"game_id": 683, "title": "Lemmings"},
        {"game_id": 38448, "title": "Lemmings"},
        {"game_id": 1603, "title": "Lemmings 2: The Tribes"},
    ]
    result = resolve_word_search_fallback(WordSearchClient({"lemmings": lemmings}), "Lemmings")
    assert result.status == "ambiguous"
    assert result.moby_game_id is None
    ids = {c["id"] for c in (result.metadata_json or {}).get("candidates", [])}
    assert ids == {683, 38448}


def test_word_search_fallback_rejects_partial_title_matches() -> None:
    result = resolve_word_search_fallback(WordSearchClient({"jetz": JETZ_SEARCH}), "Jetz Racing")
    assert result.status == "no_match"
    assert result.block_reason == "moby_word_search_no_match"


def test_word_search_fallback_limits_queries_to_distinctive_words() -> None:
    client = WordSearchClient({})
    resolve_word_search_fallback(client, "The Legend of the Hidden Temple Game", max_queries=2)
    assert len(client.queries) == 2
    assert all(q not in {"the", "of", "game"} for q in client.queries)


def test_resolve_for_game_tries_word_search_before_web_search(monkeypatch, tmp_path) -> None:
    import moby_resolve

    class FakeMobyClient(WordSearchClient):
        def __init__(self, cfg: dict, credentials_path: Path):
            super().__init__({"jetz": JETZ_SEARCH})

        def resolve(self, search_title: str) -> MobyResult:
            return MobyResult("no_match", "moby_no_match", None, None, None, None)

    monkeypatch.setattr(
        moby_resolve,
        "load_mobygames_config",
        lambda root: {"enabled": True, "web_search_fallback": {"enabled": True}},
    )
    monkeypatch.setattr(moby_resolve, "moby_credentials_path", lambda root, cfg: tmp_path / "creds.yaml")
    monkeypatch.setattr(moby_resolve, "MobyClient", FakeMobyClient)

    def no_web_search(*args: object, **kwargs: object) -> MobyResult:
        raise AssertionError("web search should not run when the API word search matched")

    monkeypatch.setattr(moby_resolve, "resolve_web_search_fallback", no_web_search)
    result = moby_resolve.resolve_for_game(
        config_root=tmp_path, folder_name="Hot_Wheels_Jetz", moby_search_title="Hot Wheels Jetz"
    )
    assert result.status == "ok"
    assert result.moby_game_id == 43459


def test_moby_title_key_treats_roman_and_arabic_numerals_alike() -> None:
    assert moby_title_key("Gothic III: Forsaken Gods") == moby_title_key("Gothic 3 Forsaken Gods")
    assert moby_title_key("Final Fantasy XII") == moby_title_key("final fantasy 12")
    assert moby_title_key("Heroes of Might and Magic IV") != moby_title_key("Heroes of Might and Magic 3")


def test_moby_title_key_only_converts_whole_word_numerals() -> None:
    assert moby_title_key("Mix Live") == "mix live"
    assert moby_title_key("Civilization") == "civilization"


def test_word_search_fallback_matches_roman_numeral_title() -> None:
    client = WordSearchClient({"forsaken": [{"game_id": 37190, "title": "Gothic 3: Forsaken Gods"}]})
    result = resolve_word_search_fallback(client, "Gothic III Forsaken Gods")
    assert result.status == "ok"
    assert result.moby_game_id == 37190


def test_moby_title_key_leaves_x_and_i_alone() -> None:
    # "Mega Man X" and "Mega Man 10" are different games; "I" is usually the word I.
    assert moby_title_key("Mega Man X") != moby_title_key("Mega Man 10")
    assert moby_title_key("I Am Alive") == "i am alive"


def test_resolve_for_game_reports_error_when_word_search_api_fails(monkeypatch, tmp_path) -> None:
    import moby_resolve

    class FailingSearchClient(WordSearchClient):
        def __init__(self, cfg: dict, credentials_path: Path):
            super().__init__({})

        def resolve(self, search_title: str) -> MobyResult:
            return MobyResult("no_match", "moby_no_match", None, None, None, None)

        def search_titles(self, title: str) -> list[dict]:
            raise RuntimeError("429 Too Many Requests")

    monkeypatch.setattr(moby_resolve, "load_mobygames_config", lambda root: {"enabled": True})
    monkeypatch.setattr(moby_resolve, "moby_credentials_path", lambda root, cfg: tmp_path / "creds.yaml")
    monkeypatch.setattr(moby_resolve, "MobyClient", FailingSearchClient)
    result = moby_resolve.resolve_for_game(
        config_root=tmp_path, folder_name="Gothic_III_Forsaken_Gods", moby_search_title="Gothic III Forsaken Gods"
    )
    assert result.status == "error"
    assert result.block_reason == "moby_api_error"


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None, body=None, url: str = ""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body if body is not None else []
        self.url = url

    def raise_for_status(self) -> None:
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error: for url: {self.url}", response=self)

    def json(self):
        return self._body


def _client_with_responses(monkeypatch, responses: list, **cfg_overrides):
    import moby_resolve

    cfg = {
        "api_base": "https://api.example.test/v1",
        "min_request_interval_seconds": 0,
        "rate_limit_wait_seconds": 300,
        "rate_limit_max_waits": 3,
        **cfg_overrides,
    }
    client = moby_resolve.MobyClient(cfg, Path("/nonexistent/creds.yaml"))
    client.api_key = "moby_SECRETKEY"
    sleeps: list[float] = []
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params or {}))
        resp = responses.pop(0)
        resp.url = f"{url}?api_key={(params or {}).get('api_key')}"
        return resp

    monkeypatch.setattr(moby_resolve.requests, "get", fake_get)
    monkeypatch.setattr(moby_resolve.time, "sleep", lambda s: sleeps.append(s))
    return client, sleeps, calls


def test_moby_client_waits_out_rate_limit_and_retries(monkeypatch) -> None:
    client, sleeps, calls = _client_with_responses(
        monkeypatch,
        [_FakeResponse(429), _FakeResponse(429), _FakeResponse(200, body=[{"game_id": 1, "title": "Rygar"}])],
    )
    assert client.search_titles("Rygar") == [{"game_id": 1, "title": "Rygar"}]
    assert len(calls) == 3
    assert sleeps.count(300) == 2


def test_moby_client_uses_retry_after_header_when_given(monkeypatch) -> None:
    client, sleeps, _ = _client_with_responses(
        monkeypatch,
        [_FakeResponse(429, headers={"Retry-After": "42"}), _FakeResponse(200, body=[])],
    )
    assert client.search_titles("Rygar") == []
    assert 42 in sleeps
    assert 300 not in sleeps


def test_moby_client_gives_up_after_max_rate_limit_waits(monkeypatch) -> None:
    import pytest

    client, sleeps, calls = _client_with_responses(monkeypatch, [_FakeResponse(429) for _ in range(4)])
    with pytest.raises(RuntimeError, match="429"):
        client.search_titles("Rygar")
    assert len(calls) == 4
    assert sleeps.count(300) == 3


def test_moby_client_errors_never_contain_the_api_key(monkeypatch) -> None:
    import pytest

    client, _, _ = _client_with_responses(monkeypatch, [_FakeResponse(500)])
    with pytest.raises(RuntimeError) as excinfo:
        client.search_titles("Rygar")
    assert "moby_SECRETKEY" not in str(excinfo.value)
    assert "500" in str(excinfo.value)


def test_moby_client_rate_limit_error_never_contains_the_api_key(monkeypatch) -> None:
    import pytest

    client, _, _ = _client_with_responses(monkeypatch, [_FakeResponse(429) for _ in range(4)])
    with pytest.raises(RuntimeError) as excinfo:
        client.search_titles("Rygar")
    assert "moby_SECRETKEY" not in str(excinfo.value)
