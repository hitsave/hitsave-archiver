"""Shared display helpers: status badges, folded error cells, timestamps."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from formatting import (  # noqa: E402
    ERROR_FULL_MAX,
    ERROR_PREVIEW_CHARS,
    error_cell,
    fmt_ts,
    moby_badge,
    status_badge,
)


def test_status_badge_classes() -> None:
    assert 'class="badge badge-ok">complete<' in status_badge("complete")
    assert "badge-fail" in status_badge("failed")
    assert "badge-fail" in status_badge("quarantine")
    assert "badge-neutral" in status_badge("pending")
    assert "(no status)" in status_badge(None)
    assert "&lt;x&gt;" in status_badge("<x>")


def test_moby_badge_classes() -> None:
    assert "badge-ok" in moby_badge("ok")
    assert "badge-warn" in moby_badge("ambiguous")
    assert "badge-neutral" in moby_badge("no_match")
    assert moby_badge(None) == moby_badge("") == '<span class="muted">—</span>'


def test_error_cell_empty_and_short() -> None:
    assert error_cell(None) == error_cell("") == ""
    assert error_cell("Not a press kit") == '<span class="err">Not a press kit</span>'


def _summary(cell: str) -> str:
    return re.search(r"<summary><span class=\"err\">(.*?)</span></summary>", cell, re.S).group(1)


def test_error_cell_folds_long_errors() -> None:
    err = "Content policy blocked booru-style filenames (23 file(s)): " + "x_" * 300
    cell = error_cell(err)
    assert cell.startswith('<details class="err-more">')
    summary = _summary(cell)
    assert summary.endswith("…")
    assert len(summary) <= ERROR_PREVIEW_CHARS + 1
    assert err in cell  # full text inside the fold


def test_error_cell_folds_multiline_on_first_line() -> None:
    cell = error_cell("Traceback: boom\n  File x.py, line 3\nValueError")
    assert _summary(cell) == "Traceback: boom…"
    assert "ValueError" in cell


def test_error_cell_escapes_and_caps() -> None:
    cell = error_cell("<script>alert(1)</script>\n" + "y" * 5000)
    assert "<script>" not in cell
    assert "&lt;script&gt;" in cell
    assert "y" * ERROR_FULL_MAX not in cell
    assert "see the item page" in cell


def test_fmt_ts_accepts_z_suffix() -> None:
    assert fmt_ts("2026-11-04T16:00:00.388421Z") == "2026-11-04 16:00 UTC"
    assert fmt_ts("") == ""


def test_problem_text_falls_back_to_block_reason_for_quarantined_and_failed() -> None:
    from formatting import problem_text

    assert problem_text({"status": "quarantine", "error_message": None, "block_reason": "duplicate of x"}) == "duplicate of x"
    assert problem_text({"status": "failed", "error_message": "", "block_reason": "virus_detected"}) == "virus_detected"
    assert problem_text({"status": "quarantine", "error_message": "Not a press kit", "block_reason": "curation_review"}) == "Not a press kit"
    # Moby match notes on complete rows aren't problems
    assert problem_text({"status": "complete", "error_message": None, "block_reason": "moby_ambiguous"}) == ""
