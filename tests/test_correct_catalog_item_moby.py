"""correct-catalog-item.py: a new MobyGames URL also brings that game's developers and publishers."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("correct_item_moby", ROOT / "scripts" / "correct-catalog-item.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

URL = "https://www.mobygames.com/game/12345/right-game/"
PIDS = {"dcterms:creator": 2, "dcterms:publisher": 5, "bibo:uri": 9}


class _Session:
    def patch(self, url, **kw):
        return type("R", (), {"ok": True, "status_code": 200, "text": ""})()


@pytest.fixture
def omeka(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(cc, "_omeka_session", lambda: (_Session(), "http://omeka/api", {"key_identity": "i", "key_credential": "c"}))
    monkeypatch.setattr(cc, "_fetch_item", lambda *a: {"o:title": "Wrong Game"})
    monkeypatch.setattr(cc.register_api, "resolve_property_id", lambda s, b, c, term: PIDS.get(term, 1))
    monkeypatch.setattr(cc.register_api, "literal_value", lambda pid, v: {"property_id": pid, "@value": v})
    monkeypatch.setattr(cc.register_api, "uri_value", lambda pid, uri, label=None: {"property_id": pid, "@id": uri})
    monkeypatch.setattr(cc.register_api, "patch_item_rdf_values",
                        lambda s, b, c, item_id, updates, item_body=None: calls.update(updates=updates))
    monkeypatch.setattr(cc, "_ledger_row_by_omeka_item_id", lambda item_id, required=True: None)
    return calls


def _names(updates: dict, term: str) -> list[str]:
    return [v["@value"] for v in updates[term]]


def _main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["correct-catalog-item.py", "--omeka-ref", "27834", *argv])
    cc.main()


def _moby(monkeypatch, developers, publishers):
    seen = []
    def lookup(url):
        seen.append(url)
        game = {"moby_game_id": 12345, "moby_title": "Right Game", "moby_url": URL, "first_release_date": "2001-10-31"}
        if developers:
            game["developers"] = developers
        if publishers:
            game["publishers"] = publishers
        return game
    monkeypatch.setattr(cc, "moby_game_for_url", lookup)
    return seen


@pytest.fixture
def ledger(monkeypatch):
    """The item has a ledger row; records what the ledger match is set to."""
    set_to: list = []
    monkeypatch.setattr(cc, "_ledger_row_by_omeka_item_id",
                        lambda item_id, required=True: {"game_key": "right-game-01", "inventory_json": {}})
    monkeypatch.setattr(cc, "_set_ledger_moby_match", lambda key, game: set_to.append((key, game)))
    stub = type("W", (), {"patch_wasabi_bag": staticmethod(lambda **kw: None)})
    monkeypatch.setattr(cc, "_patch_wasabi_module", lambda: stub)
    return set_to


def test_new_moby_url_replaces_developers_and_publishers(omeka, monkeypatch, capsys) -> None:
    seen = _moby(monkeypatch, ["Right Dev"], ["Right Pub", "Other Pub"])
    _main(monkeypatch, "--moby-url", URL)
    assert seen == [URL]
    assert _names(omeka["updates"], "dcterms:creator") == ["Right Dev"]
    assert _names(omeka["updates"], "dcterms:publisher") == ["Right Pub", "Other Pub"]
    assert "bibo:uri" in omeka["updates"]
    assert "Right Pub" in capsys.readouterr().out


def test_names_typed_in_the_form_win_over_mobygames(omeka, monkeypatch) -> None:
    _moby(monkeypatch, ["Right Dev"], ["Right Pub"])
    _main(monkeypatch, "--moby-url", URL, "--developer", "My Dev", "--clear-publisher")
    assert _names(omeka["updates"], "dcterms:creator") == ["My Dev"]
    assert omeka["updates"]["dcterms:publisher"] == []


def test_a_role_mobygames_doesnt_list_is_left_alone(omeka, monkeypatch, capsys) -> None:
    _moby(monkeypatch, ["Right Dev"], [])
    _main(monkeypatch, "--moby-url", URL)
    assert _names(omeka["updates"], "dcterms:creator") == ["Right Dev"]
    assert "dcterms:publisher" not in omeka["updates"]
    assert "no publishers" in capsys.readouterr().err


def test_failed_lookup_still_corrects_the_url(omeka, monkeypatch, capsys) -> None:
    def broken(url):
        raise RuntimeError("moby_credentials_missing")
    monkeypatch.setattr(cc, "moby_game_for_url", broken)
    _main(monkeypatch, "--moby-url", URL)
    assert set(omeka["updates"]) == {"bibo:uri"}
    err = capsys.readouterr().err
    assert "moby_credentials_missing" in err and "left unchanged" in err


def test_no_lookup_without_a_new_moby_url(omeka, monkeypatch) -> None:
    seen = _moby(monkeypatch, ["X"], ["Y"])
    _main(monkeypatch, "--title", "New title")
    assert seen == []


def test_new_moby_url_sets_the_ledger_match_to_the_new_game(omeka, ledger, monkeypatch) -> None:
    _moby(monkeypatch, ["Right Dev"], ["Right Pub"])
    _main(monkeypatch, "--moby-url", URL, "--no-execute")
    assert len(ledger) == 1
    key, game = ledger[0]
    assert key == "right-game-01"
    assert game["moby_game_id"] == 12345 and game["moby_title"] == "Right Game"
    assert game["first_release_date"] == "2001-10-31"
    assert game["moby_match_source"] == "staff_correction"


def test_failed_lookup_leaves_the_ledger_match_alone(omeka, ledger, monkeypatch) -> None:
    def broken(url):
        raise RuntimeError("moby_api_error")
    monkeypatch.setattr(cc, "moby_game_for_url", broken)
    _main(monkeypatch, "--moby-url", URL, "--no-execute")
    assert ledger == []
