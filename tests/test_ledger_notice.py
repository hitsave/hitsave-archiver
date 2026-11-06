"""Ledger notices: actions say what happened after the redirect, and redirects stay local."""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from notice import render_notice, safe_return_path, visibility_message, with_notice  # noqa: E402


def test_return_path_refuses_protocol_relative() -> None:
    assert safe_return_path("//evil.example/x", "") == "/"
    assert safe_return_path("https://evil.example/", "") == "/"


def test_return_path_keeps_local_path_with_query() -> None:
    assert safe_return_path("/item?key=a", "") == "/item?key=a"


def test_return_path_falls_back_to_referer_path() -> None:
    assert safe_return_path("", "https://ledger.hitsave.org/health?sort=x") == "/health"
    assert safe_return_path("", "https://ledger.hitsave.org//evil.example") == "/"
    assert safe_return_path("", "") == "/"


def test_with_notice_keeps_existing_query() -> None:
    url = with_notice("/item?key=a+b", "ok", "Done")
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert parsed.path == "/item"
    assert qs == {"key": ["a b"], "notice": ["ok"], "msg": ["Done"]}


def test_with_notice_replaces_old_notice() -> None:
    url = with_notice("/?notice=fail&msg=old", "ok", "new")
    assert parse_qs(urlparse(url).query) == {"notice": ["ok"], "msg": ["new"]}


def test_render_notice_escapes_and_styles() -> None:
    out = render_notice({"notice": ["fail"], "msg": ["<b>x"]})
    assert "&lt;b&gt;x" in out
    assert "notice-fail" in out
    assert "<b>" not in out


def test_render_notice_ignores_unknown_kind_and_empty() -> None:
    assert render_notice({"notice": ["weird"], "msg": ["x"]}) == ""
    assert render_notice({"notice": ["ok"], "msg": [""]}) == ""
    assert render_notice({}) == ""


def test_visibility_success_messages() -> None:
    assert visibility_message("1234", True, 0, "{}") == ("ok", "Item 1234 is now public.")
    assert visibility_message("1234", False, 0, "") == ("ok", "Item 1234 is now private.")


def test_visibility_failure_carries_last_error_line() -> None:
    out = "Traceback (most recent call last):\n  ...\nRuntimeError: PATCH items 1234 visibility failed (500)\n\n"
    kind, msg = visibility_message("1234", True, 1, out)
    assert kind == "fail"
    assert msg.startswith("Couldn't publish item 1234: ")
    assert msg.endswith("PATCH items 1234 visibility failed (500)")
    kind, msg = visibility_message("9", False, 1, "")
    assert (kind, msg) == ("fail", "Couldn't hide item 9: no output from the visibility script.")
