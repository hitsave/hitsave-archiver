"""Ledger Health page: intake failures that never reached the ledger."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from intake_failures import open_failures, render_intake_failures, scan_failed_dir  # noqa: E402


def _fail(root: Path, slug: str, reason: str | None, mtime: float) -> None:
    d = root / slug
    d.mkdir(parents=True)
    target = d
    if reason is not None:
        (d / "error.txt").write_text(reason + "\nTraceback line\n")
        target = d / "error.txt"
    os.utime(target, (mtime, mtime))


class _Conn:
    def __init__(self, complete_sources):
        self.complete = set(complete_sources)
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.queries.append((sql, params))
        rows = [(s,) for s in params[0] if s in self.complete]

        class R:
            def fetchall(self_inner):
                return rows

        return R()


def test_scan_lists_newest_first_with_first_line(tmp_path: Path) -> None:
    _fail(tmp_path, "redump-old", "SHA-1 mismatch for disc.iso", 1_000_000)
    _fail(tmp_path, "redump-new", "submission info not found", 2_000_000)
    _fail(tmp_path, "redump-noerr", None, 1_500_000)
    (tmp_path / "stray.txt").write_text("not a package")
    got = scan_failed_dir(tmp_path)
    assert [f.slug for f in got] == ["redump-new", "redump-noerr", "redump-old"]
    assert got[0].reason == "submission info not found"
    assert got[1].reason == "(no error.txt)"


def test_missing_dir_is_empty(tmp_path: Path) -> None:
    assert scan_failed_dir(tmp_path / "nope") == []
    assert open_failures(lambda: _Conn([]), tmp_path / "nope") == ([], 0)


def test_preserved_later_is_hidden(tmp_path: Path) -> None:
    _fail(tmp_path, "redump-fixed", "SHA-1 mismatch", 1_000_000)
    _fail(tmp_path, "redump-broken", "MD5 mismatch", 2_000_000)
    conn = _Conn(["/data/intake/ready/redump-fixed"])
    open_, resolved = open_failures(lambda: conn, tmp_path)
    assert [f.slug for f in open_] == ["redump-broken"]
    assert resolved == 1
    page = render_intake_failures(open_, resolved, failed_dir=tmp_path)
    assert "redump-broken" in page and "MD5 mismatch" in page
    assert "redump-fixed" not in page and "1 older failure(s) not shown" in page


def test_render_escapes_and_handles_empty(tmp_path: Path) -> None:
    _fail(tmp_path, "redump-x", "bad <script>alert(1)</script>", 1_000_000)
    page = render_intake_failures(scan_failed_dir(tmp_path), 0, failed_dir=tmp_path)
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "No open intake failures" in render_intake_failures([], 0, failed_dir=tmp_path)


def test_render_says_unavailable_when_folder_not_mounted(tmp_path: Path) -> None:
    page = render_intake_failures([], 0, failed_dir=tmp_path / "not-mounted")
    assert "aren't available here" in page and "No open intake failures" not in page
