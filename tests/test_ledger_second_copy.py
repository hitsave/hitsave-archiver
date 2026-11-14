"""The ledger shows each package's second copy (package_copy) on its item page and on Health."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

import ledger_health_report as lhr  # noqa: E402
from filters import flag_chip  # noqa: E402
from item_page import render_item_page, second_copy_line  # noqa: E402

NOV20 = datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc)
NOV14 = datetime(2026, 11, 14, 6, 15, tzinfo=timezone.utc)


def copy(**over):
    row = {"location": "aws-deep-archive", "copied_at": NOV14, "verified_at": NOV20, "out_of_date_since": None}
    row.update(over)
    return row


def test_verified_copy():
    assert second_copy_line(copy()) == "Second copy: verified 2026-11-20 (AWS Glacier Deep Archive)"


def test_copy_not_verified_yet():
    assert second_copy_line(copy(verified_at=None)) == (
        "Second copy: copied 2026-11-14, not verified yet (AWS Glacier Deep Archive)")


def test_no_copy():
    assert second_copy_line(None) == "Second copy: none yet"


def test_out_of_date_copy():
    assert second_copy_line(copy(out_of_date_since=NOV20)) == (
        "Second copy: out of date since 2026-11-20 (AWS Glacier Deep Archive)")


def _page(row, second_copy):
    return render_item_page(row, admin_base="a", site_base="s", is_public=None, status_config={},
                            second_copy=second_copy).decode()


def test_item_page_shows_the_line_for_complete_packages_only():
    complete = {"game_key": "k", "status": "complete", "omeka_item_id": None}
    assert "Second copy: verified 2026-11-20" in _page(complete, copy())
    assert "Second copy: none yet" in _page(complete, None)
    assert "Second copy" not in _page({**complete, "status": "failed"}, None)


def test_health_flag_counts_complete_packages_without_an_up_to_date_copy():
    cond, label = lhr.FLAG_CONDITIONS["no_second_copy"]
    assert "status = 'complete'" in cond and "package_copy" in cond and "out_of_date_since IS NULL" in cond
    assert label == "complete, no up-to-date second copy"
    assert lhr.FLAG_CONDITIONS["no_second_copy"][0] in lhr.NO_SECOND_COPY_COUNT_SQL
    assert 'href="/?flag=no_second_copy"' in flag_chip("no_second_copy", 3)
