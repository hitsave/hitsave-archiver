"""Correction form: current Omeka values, the per-field change plan, and the Windmill args."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from correct_review import (  # noqa: E402
    Change,
    changed_since,
    current_values,
    plan_changes,
    side_effects,
    windmill_args,
)
from omeka_status import fetch_item_json  # noqa: E402

ITEM = {
    "o:title": "Foo Bar — batch 3",
    "dcterms:title": [{"@value": "Foo Bar"}],
    "dcterms:description": [{"@value": "Line one.\r\nLine two."}, {"@value": "Second value."}],
    "dcterms:creator": [{"@value": "Dev A"}, {"@value": "Dev B"}],
    "dcterms:publisher": [{"@value": "Pub"}],
    "dcterms:provenance": [{"@value": "Anthony Perkins"}],
    "bibo:uri": [
        {"@id": "s3://bucket/mobygames.com/aip"},
        {"@id": "https://example.org/ref"},
        {"@id": "https://www.mobygames.com/game/123/foo-bar/"},
    ],
}
CURRENT = {
    "title": "Foo Bar",
    "moby_url": "https://www.mobygames.com/game/123/foo-bar/",
    "description": "Line one.\nLine two.\n\nSecond value.",
    "developer": "Dev A, Dev B",
    "publisher": "Pub",
    "donor": "Anthony Perkins",
}


def test_current_values_from_item() -> None:
    assert current_values(ITEM) == CURRENT


def test_current_values_fallbacks() -> None:
    cur = current_values({"o:title": "Only o:title", "bibo:uri": [{"@id": "https://example.org/x"}]})
    assert cur == {"title": "Only o:title", "moby_url": "", "description": "", "developer": "", "publisher": "", "donor": ""}


def test_unchanged_form_is_nothing_to_change() -> None:
    changes, errors = plan_changes(CURRENT, dict(CURRENT), clear_moby=False)
    assert changes == []
    assert any("Nothing to change" in e for e in errors)


def test_whitespace_and_name_separators_are_not_changes() -> None:
    form = dict(CURRENT, title="  Foo Bar ", developer="Dev A ;Dev B", description="Line one.\r\nLine two.\r\n\r\nSecond value.\n")
    changes, _ = plan_changes(CURRENT, form, clear_moby=False)
    assert changes == []


def test_edit_sets_and_empty_names_clear() -> None:
    form = dict(CURRENT, title="Foo Bar Deluxe", developer="", publisher="Pub, Pub Two")
    changes, errors = plan_changes(CURRENT, form, clear_moby=False)
    assert errors == []
    assert changes == [
        Change("title", "Foo Bar", "Foo Bar Deluxe", "set"),
        Change("developer", "Dev A, Dev B", "", "clear"),
        Change("publisher", "Pub", "Pub, Pub Two", "set"),
    ]


def test_emptying_title_description_or_moby_is_an_error() -> None:
    for field, hint in (("title", "title"), ("description", "Remove the MobyGames match"), ("moby_url", "Remove the MobyGames match")):
        changes, errors = plan_changes(CURRENT, dict(CURRENT, **{field: ""}), clear_moby=False)
        assert errors and hint in errors[0], (field, errors)


def test_clearing_already_empty_names_is_no_change() -> None:
    cur = dict(CURRENT, publisher="")
    changes, errors = plan_changes(cur, dict(cur, title="New"), clear_moby=False)
    assert [c.field for c in changes] == ["title"]


def test_clear_moby_alone_is_a_change_but_not_with_new_url() -> None:
    changes, errors = plan_changes(CURRENT, dict(CURRENT), clear_moby=True)
    assert errors == [] and changes == []
    _, errors = plan_changes(CURRENT, dict(CURRENT, moby_url="https://www.mobygames.com/game/9/x/"), clear_moby=True)
    assert errors and "not both" in errors[0]
    # emptying the URL together with the checkbox is fine
    _, errors = plan_changes(CURRENT, dict(CURRENT, moby_url=""), clear_moby=True)
    assert errors == []


def test_changed_since() -> None:
    assert changed_since(CURRENT, dict(CURRENT)) == []
    assert changed_since(CURRENT, dict(CURRENT, publisher="Someone else")) == ["publisher"]


def test_windmill_args_map_changes() -> None:
    changes = [
        Change("title", "Foo Bar", "Foo Bar Deluxe", "set"),
        Change("developer", "Dev A, Dev B", "", "clear"),
        Change("description", "x", "New text", "set"),
    ]
    args = windmill_args("123", changes, clear_moby=False, former_title="", correction_summary="note", execute=True)
    assert args == {
        "omeka_ref": "123",
        "title": "Foo Bar Deluxe",
        "clear_developer": True,
        "description": "New text",
        "correction_summary": "note",
    }
    args = windmill_args("123", [], clear_moby=True, former_title="Old", correction_summary="", execute=False)
    assert args == {"omeka_ref": "123", "clear_moby_url": True, "former_title": "Old", "execute": False}


def test_side_effects_in_plain_words() -> None:
    title = [Change("title", "a", "b", "set")]
    assert any("Wasabi" in s for s in side_effects(title, clear_moby=False, execute=True))
    assert any("left as they are" in s for s in side_effects(title, clear_moby=False, execute=False))
    assert not any("Wasabi" in s for s in side_effects([Change("publisher", "a", "b", "set")], clear_moby=False, execute=True))
    moby = " ".join(side_effects([], clear_moby=True, execute=True))
    assert "release date" in moby and "press kit" in moby and "developers and publishers" in moby
    kept = " ".join(side_effects([Change("description", "a", "b", "set")], clear_moby=True, execute=True))
    assert "press kit" not in kept


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


def _config(tmp_path: Path) -> Path:
    (tmp_path / "secrets").mkdir()
    (tmp_path / "omeka-catalog.yaml").write_text(
        "api:\n  base_url: http://omeka/api\n  credentials_file: config/secrets/omeka.yaml\n"
    )
    (tmp_path / "secrets" / "omeka.yaml").write_text("key_identity: id\nkey_credential: cred\n")
    return tmp_path


def test_fetch_item_json(tmp_path) -> None:
    root = _config(tmp_path)
    assert fetch_item_json(5, get=lambda u, **k: _Resp(200, ITEM), config_root=root) == ITEM
    assert fetch_item_json(5, get=lambda u, **k: _Resp(500, {}), config_root=root) is None
    assert fetch_item_json(5, get=lambda u, **k: _Resp(200, []), config_root=root) is None


def test_donor_sets_clears_and_maps_to_windmill() -> None:
    changes, errors = plan_changes(CURRENT, dict(CURRENT, donor="Anthony Perkins; Brock"), clear_moby=False)
    assert errors == [] and changes == [Change("donor", "Anthony Perkins", "Anthony Perkins, Brock", "set")]
    cleared, errors = plan_changes(CURRENT, dict(CURRENT, donor=""), clear_moby=False)
    assert errors == [] and cleared == [Change("donor", "Anthony Perkins", "", "clear")]
    args = windmill_args("16396", changes + cleared[:0], clear_moby=False, former_title="", correction_summary="", execute=True)
    assert args == {"omeka_ref": "16396", "donor": "Anthony Perkins, Brock"}
    assert windmill_args("1", cleared, clear_moby=False, former_title="", correction_summary="", execute=True) == {
        "omeka_ref": "1", "clear_donor": True}
    # A donor change touches neither Wasabi nor the MobyGames match; removing the match keeps donors.
    assert side_effects(changes, clear_moby=False, execute=True) == []
    moby = " ".join(side_effects([], clear_moby=True, execute=True)).casefold()
    assert "donated" not in moby
    kept = " ".join(side_effects([Change("donor", "a", "b", "set")], clear_moby=True, execute=True))
    assert "developers and publishers from the match are removed" in kept


def test_new_moby_url_says_developers_and_publishers_follow() -> None:
    url = [Change("moby_url", "https://www.mobygames.com/game/1/a/", "https://www.mobygames.com/game/2/b/", "set")]
    both = " ".join(side_effects(url, clear_moby=False, execute=True))
    assert "developers and publishers" in both and "MobyGames lists" in both
    typed = url + [Change("developer", "Old", "Mine", "set")]
    one = " ".join(side_effects(typed, clear_moby=False, execute=True))
    assert "publishers" in one and "developers and publishers" not in one
    all_typed = typed + [Change("publisher", "", "", "clear")]
    assert "MobyGames lists" not in " ".join(side_effects(all_typed, clear_moby=False, execute=True))
