"""Ledger "Disc contents" page and the staff decision script."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from disc_contents_review import render_disc_contents_page, run_decision, summary_text  # noqa: E402

_spec = importlib.util.spec_from_file_location("set_dc", ROOT / "scripts" / "set-disc-contents-display.py")
set_dc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(set_dc)

SUMMARY = {"files": 360, "bytes": 232918961, "by_kind": {"image": {"files": 281, "bytes": 1},
                                                         "video": {"files": 13, "bytes": 1},
                                                         "program": {"files": 2, "bytes": 1}}}


def test_summary_text() -> None:
    assert summary_text(SUMMARY) == "281 images, 13 videos, 2 programs · 233 MB"
    assert summary_text(None) == "not extracted yet"
    assert summary_text('{"files": 0, "bytes": 0, "by_kind": {}}') == "no files · 0 MB"


def test_page_lists_pending_with_decision_buttons() -> None:
    rows = [
        {"game_key": "bonus-disc-01", "omeka_item_id": 42, "disc_contents_display": "review",
         "disc_contents_reasons": "Category 'Bonus Discs': needs review", "disc_contents_media_id": 7,
         "disc_contents_summary": SUMMARY},
        {"game_key": "coverdisc-02", "omeka_item_id": 43, "disc_contents_display": "review",
         "disc_contents_reasons": "", "disc_contents_media_id": None, "disc_contents_summary": None},
    ]
    page = render_disc_contents_page(rows=rows, decided=[], status_config={}, admin_base="https://x/admin").decode()
    assert "2 waiting" in page and "needs review" in page
    assert page.count('action="/disc-contents/decide"') == 1  # only the one with an attached media
    assert 'value="public"' in page and 'value="none"' in page and "https://x/admin/item/42/show" in page
    assert "still being extracted" in page


def test_empty_page_and_bad_decision() -> None:
    page = render_disc_contents_page(rows=[], decided=[], status_config={}, admin_base="").decode()
    assert "Nothing waiting for review." in page
    assert run_decision("k", "maybe", "me")[0] == 1


def test_media_public_only_on_public_items() -> None:
    assert set_dc.media_visibility("public", True) is True
    assert set_dc.media_visibility("public", False) is False
    assert set_dc.media_visibility("none", True) is False
    assert set_dc.decision_note("public", "jonas", "2026-11-07T00:00:00+00:00").startswith("staff: published by jonas")
