"""Ledger /quarantine asks for confirmation (with the record it will hide) before running."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from catalog_quarantine import needs_confirmation, render_quarantine_page  # noqa: E402

FORM = {"omeka_ref": ["1234"], "block_reason": ["not_press_material"], "note": ["Fan art <not> press"]}
HINT = {"game_key": "redump-psx-foo bar", "omeka_item_id": 1234, "moby_title": "Foo", "status": "complete"}


def test_needs_confirmation_until_confirmed() -> None:
    assert needs_confirmation({}) is True
    assert needs_confirmation({"confirmed": ["no"]}) is True
    assert needs_confirmation({"confirmed": ["yes"]}) is False


def test_confirm_page_carries_fields_and_shows_record() -> None:
    page = render_quarantine_page(form=FORM, hint=HINT, is_post=True, confirm=True).decode()
    assert 'name="confirmed" value="yes"' in page
    assert "Fan art &lt;not&gt; press" in page
    assert 'name="block_reason" value="not_press_material"' in page
    assert "Yes, quarantine" in page
    assert "redump-psx-foo bar" in page
    assert "/item?key=redump-psx-foo+bar" in page  # cancel goes back to the item


def test_confirm_page_warns_without_ledger_row() -> None:
    page = render_quarantine_page(form=FORM, hint=None, is_post=True, confirm=True).decode()
    assert "No ledger row" in page
    assert 'href="/quarantine' in page


def test_plain_form_has_no_confirmed_field() -> None:
    page = render_quarantine_page(form={}, hint=None).decode()
    assert 'name="confirmed"' not in page
