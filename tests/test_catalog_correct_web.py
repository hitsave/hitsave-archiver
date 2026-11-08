"""Ledger catalog correction pages: prefilled form → review → apply (Windmill), with fakes."""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from catalog_correct import correct_get, correct_post  # noqa: E402

ITEM = {
    "o:title": "Foo <Bar>",
    "dcterms:title": [{"@value": "Foo <Bar>"}],
    "dcterms:description": [{"@value": "Press text."}],
    "dcterms:creator": [{"@value": "Dev A"}, {"@value": "Dev B"}],
    "dcterms:publisher": [{"@value": "Pub"}],
    "bibo:uri": [{"@id": "https://www.mobygames.com/game/1/foo/"}],
}
HINT = {"game_key": "redump-psx-foo", "omeka_item_id": 42, "moby_title": "Foo", "status": "complete"}


class Fakes:
    def __init__(self, item=ITEM):
        self.item = item
        self.runs: list[dict] = []
        self.audits: list[str] = []

    def kw(self):
        return {
            "fetch_item": lambda item_id: self.item,
            "hint_for": lambda ref: HINT,
            "run": self.run,
            "audit": self.audits.append,
            "status_config": {},
        }

    def run(self, args):
        self.runs.append(args)
        return 0, "patched dcterms:title"


def _get(qs, fakes=None):
    return correct_get(qs, **(fakes or Fakes()).kw()).decode()


def _hidden(page: str) -> dict[str, list[str]]:
    """The form a browser would submit from this page (inputs + textareas, no buttons)."""
    import html as h

    form: dict[str, list[str]] = {}
    for name, value in re.findall(r'<input type="(?:hidden|text)" name="([^"]+)" value="([^"]*)"', page):
        form.setdefault(name, []).append(h.unescape(value))
    for name, value in re.findall(r'<textarea name="([^"]+)"[^>]*>(.*?)</textarea>', page, re.S):
        form.setdefault(name, []).append(h.unescape(value))
    for name, value in re.findall(r'<input type="checkbox" name="([^"]+)" value="([^"]*)" checked', page):
        form.setdefault(name, []).append(value)
    return form


def _post(form, step, fakes):
    return correct_post({**form, "step": [step]}, **fakes.kw()).decode()


def test_lookup_page_without_ref() -> None:
    page = _get({})
    assert 'method="get"' in page and 'name="omeka_ref"' in page
    assert "Review changes" not in page


def test_lookup_page_when_omeka_unreadable() -> None:
    page = _get({"omeka_ref": ["42"]}, Fakes(item=None))
    assert "Couldn't read item 42 from Omeka" in html.unescape(page)
    assert "Review changes" not in page
    assert "Not an Omeka item" in _get({"omeka_ref": ["nonsense"]})


def test_form_is_prefilled_escaped_and_carries_originals() -> None:
    page = _get({"omeka_ref": ["https://archive.hitsave.org/admin/item/42"]})
    form = _hidden(page)
    assert form["omeka_ref"] == ["42"]
    assert form["title"] == ["Foo <Bar>"] and form["orig_title"] == ["Foo <Bar>"]
    assert form["developer"] == ["Dev A, Dev B"]
    assert form["description"] == ["Press text."]
    assert "<Bar>" not in page
    assert "redump-psx-foo" in page and "/item?key=redump-psx-foo" in page
    assert 'name="execute" value="true" checked' in page


def test_review_lists_only_changes_and_side_effects() -> None:
    fakes = Fakes()
    form = _hidden(_get({"omeka_ref": ["42"]}, fakes))
    form.update(title=["Foo Deluxe"], developer=[""])
    page = _post(form, "review", fakes)
    assert "Check the changes" in page
    assert "Foo Deluxe" in page and "Foo &lt;Bar&gt;" in page
    assert "Developers" in page and "(removed)" in page
    assert "Publishers" not in page.split("Also happens")[0].split("<table")[1]
    assert "Wasabi" in page
    assert 'name="step" value="apply"' in page and 'name="step" value="edit"' in page
    assert fakes.runs == []


def test_review_errors_go_back_to_form() -> None:
    fakes = Fakes()
    form = _hidden(_get({"omeka_ref": ["42"]}, fakes))
    page = _post(form, "review", fakes)
    assert "Nothing to change" in page and "Review changes" in page
    form["title"] = [""]
    assert "title can't be empty" in html.unescape(_post(form, "review", fakes))


def test_edit_returns_form_with_edits_kept() -> None:
    fakes = Fakes()
    form = _hidden(_get({"omeka_ref": ["42"]}, fakes))
    form["title"] = ["Foo Deluxe"]
    review = _post(form, "review", fakes)
    page = _post(_hidden(review), "edit", fakes)
    assert _hidden(page)["title"] == ["Foo Deluxe"]
    assert _hidden(page)["orig_title"] == ["Foo <Bar>"]


def test_apply_runs_windmill_with_only_the_changes() -> None:
    fakes = Fakes()
    form = _hidden(_get({"omeka_ref": ["42"]}, fakes))
    form.update(title=["Foo Deluxe"], developer=[""], correction_summary=["typo (by a@b)"])
    review = _post(form, "review", fakes)
    page = _post(_hidden(review), "apply", fakes)
    assert fakes.runs == [
        {"omeka_ref": "42", "title": "Foo Deluxe", "clear_developer": True, "correction_summary": "typo (by a@b)"}
    ]
    assert fakes.audits == ["42"]
    assert "Correction applied" in page and "patched dcterms:title" in page
    assert "/item?key=redump-psx-foo" in page


def test_clear_moby_reviews_and_applies() -> None:
    fakes = Fakes()
    form = _hidden(_get({"omeka_ref": ["42"]}, fakes))
    form["clear_moby"] = ["true"]
    review = _post(form, "review", fakes)
    assert "release date" in review and "(removed)" in review
    _post(_hidden(review), "apply", fakes)
    assert fakes.runs == [{"omeka_ref": "42", "clear_moby_url": True}]


def test_stale_item_stops_apply_and_refreshes_untouched_fields() -> None:
    fakes = Fakes()
    form = _hidden(_get({"omeka_ref": ["42"]}, fakes))
    form["title"] = ["Foo Deluxe"]
    review = _post(form, "review", fakes)
    fakes.item = dict(ITEM, **{"dcterms:publisher": [{"@value": "New Pub"}]})
    page = _post(_hidden(review), "apply", fakes)
    assert fakes.runs == []
    assert "changed Publishers in Omeka" in page
    again = _hidden(page)
    assert again["title"] == ["Foo Deluxe"]  # own edit kept
    assert again["publisher"] == ["New Pub"] and again["orig_publisher"] == ["New Pub"]


def test_failed_windmill_run_shows_failure() -> None:
    fakes = Fakes()
    fakes.run = lambda args: (1, "Omeka 500")
    form = _hidden(_get({"omeka_ref": ["42"]}, fakes))
    form["title"] = ["Foo Deluxe"]
    page = _post(_hidden(_post(form, "review", fakes)), "apply", fakes)
    assert "Correction failed" in page and "Omeka 500" in page


def test_return_to_is_carried_to_the_result_and_must_be_local() -> None:
    fakes = Fakes()
    page = _get({"omeka_ref": ["42"], "return_to": ["/review?moby=ok&after=11"]}, fakes)
    form = _hidden(page)
    assert form["return_to"] == ["/review?moby=ok&after=11"]
    assert 'href="/review?moby=ok&amp;after=11">Cancel' in page
    form["title"] = ["Foo Deluxe"]
    result = _post(_hidden(_post(form, "review", fakes)), "apply", fakes)
    assert 'href="/review?moby=ok&amp;after=11">Back to the review queue</a>' in result
    evil = _hidden(_get({"omeka_ref": ["42"], "return_to": ["//evil.example/x"]}, fakes))
    assert evil["return_to"] == [""]


def test_donated_by_field_round_trip() -> None:
    fakes = Fakes(dict(ITEM, **{"dcterms:provenance": [{"@value": "Anthony Perkins"}]}))
    page = _get({"omeka_ref": ["42"]}, fakes)
    assert "Donated by" in page
    form = _hidden(page)
    assert form["donor"] == ["Anthony Perkins"] and form["orig_donor"] == ["Anthony Perkins"]
    form.update(donor=["Anthony Perkins, Brock of Nostalgist's Archive"])
    review = _post(form, "review", fakes)
    assert "Donated by" in review and "Brock of Nostalgist" in review
    _post(_hidden(review), "apply", fakes)
    assert fakes.runs and fakes.runs[-1]["donor"] == "Anthony Perkins, Brock of Nostalgist's Archive"
    assert set(fakes.runs[-1]) <= {"omeka_ref", "donor", "execute"}
