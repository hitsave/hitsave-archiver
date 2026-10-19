#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from moby_web_search import duckduckgo_moby_game_candidates, moby_game_id_from_url  # noqa: E402


def test_moby_game_id_from_url() -> None:
    assert moby_game_id_from_url("https://www.mobygames.com/game/70724/tekken-card-tournament/") == 70724
    assert moby_game_id_from_url("https://example.com/") is None


def test_duckduckgo_moby_game_candidates_dedupes(monkeypatch) -> None:
    rows = [
        {"href": "https://www.mobygames.com/game/100/foo/"},
        {"href": "https://www.mobygames.com/game/100/foo/"},
        {"href": "https://www.mobygames.com/game/200/bar/"},
    ]

    def fake_ddg(query: str, *, max_results: int):
        assert "mobygames" in query
        return rows[:max_results]

    monkeypatch.setattr(
        "moby_web_search._ddg_text_results",
        lambda query, max_results=10: fake_ddg(query, max_results=max_results),
    )
    moby_cfg = {"public_game_url_template": "https://www.mobygames.com/game/{game_id}"}
    ids = duckduckgo_moby_game_candidates("Foo Game", moby_cfg, max_results=5)
    assert ids == [
        (100, "https://www.mobygames.com/game/100/"),
        (200, "https://www.mobygames.com/game/200/"),
    ]
