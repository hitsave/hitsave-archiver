"""Ledger item page: one package with all its fields, its catalog state and its actions."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from formatting import fmt_ts, item_href  # noqa: E402
from item_page import fetch_item, render_item_not_found, render_item_page, row_links  # noqa: E402
from omeka_status import fetch_item_is_public  # noqa: E402

ADMIN = "https://archive.hitsave.org/admin"
SITE = "https://archive.hitsave.org/s/hitsave"


def _row(**over):
    row = {
        "id": 7,
        "game_key": "redump-psx-foo bar",
        "status": "complete",
        "moby_status": "matched",
        "moby_title": "Foo Bar",
        "omeka_item_id": 1234,
        "omeka_media_id": 99,
        "block_reason": None,
        "error_message": None,
        "updated_at": datetime(2026, 11, 6, 12, 34, 56, tzinfo=timezone.utc),
        "metadata": {"a": 1},
    }
    row.update(over)
    return row


def _page(row, is_public):
    return render_item_page(row, admin_base=ADMIN, site_base=SITE, is_public=is_public, status_config={}).decode()


# formatting

def test_fmt_ts_converts_to_utc_minutes() -> None:
    cet = timezone(timedelta(hours=1))
    assert fmt_ts(datetime(2026, 11, 6, 13, 34, 59, tzinfo=cet)) == "2026-11-06 12:34 UTC"
    assert fmt_ts(datetime(2026, 11, 6, 12, 34)) == "2026-11-06 12:34 UTC"
    assert fmt_ts(None) == "—"
    assert fmt_ts("x") == "x"


def test_item_href_round_trips_odd_keys() -> None:
    key = "a b&c/ü?#"
    assert parse_qs(urlparse(item_href(key)).query) == {"key": [key]}


# omeka_status

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


def test_is_public_read_from_omeka(tmp_path) -> None:
    calls = []

    def get(url, **kw):
        calls.append(url)
        return _Resp(200, {"o:is_public": False})

    assert fetch_item_is_public(1234, get=get, config_root=_config(tmp_path)) is False
    assert calls[0].startswith("http://omeka/api/items/1234?")
    assert "key_identity=id" in calls[0]


def test_is_public_unknown_on_errors(tmp_path) -> None:
    root = _config(tmp_path)

    def boom(url, **kw):
        raise OSError("down")

    assert fetch_item_is_public(1, get=boom, config_root=root) is None
    assert fetch_item_is_public(1, get=lambda u, **k: _Resp(404, {}), config_root=root) is None
    assert fetch_item_is_public(1, get=lambda u, **k: _Resp(200, ["x"]), config_root=root) is None
    assert fetch_item_is_public(1, get=boom, config_root=tmp_path / "missing") is None


# fetch_item

class _Cur:
    description = [("game_key",), ("status",)]

    def __init__(self, row):
        self.row = row
        self.args = None

    def execute(self, sql, args):
        self.args = args

    def fetchone(self):
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, row):
        self.cur = _Cur(row)

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_item_returns_dict_or_none() -> None:
    conn = _Conn(("k 1", "complete"))
    assert fetch_item(lambda: conn, "k 1") == {"game_key": "k 1", "status": "complete"}
    assert conn.cur.args == ("k 1",)
    assert fetch_item(lambda: _Conn(None), "nope") is None


# render

def test_public_item_offers_hide_only() -> None:
    page = _page(_row(), True)
    assert 'name="is_public" value="false"' in page
    assert 'name="is_public" value="true"' not in page
    assert "Public" in page


def test_private_item_offers_publish_only() -> None:
    page = _page(_row(), False)
    assert 'name="is_public" value="true"' in page
    assert 'name="is_public" value="false"' not in page


def test_unknown_visibility_offers_both() -> None:
    page = _page(_row(), None)
    assert 'name="is_public" value="true"' in page
    assert 'name="is_public" value="false"' in page
    assert "couldn't reach Omeka" in page


def test_visibility_forms_return_to_item_and_confirm() -> None:
    page = _page(_row(), False)
    assert 'name="return_to" value="/item?key=redump-psx-foo+bar"' in page
    assert "return confirm(" in page
    assert "data-busy" in page


def test_links_to_omeka_and_actions() -> None:
    page = _page(_row(), True)
    assert f"{ADMIN}/item/1234/show" in page
    assert f"{SITE}/item/1234" in page
    assert "/correct?omeka_ref=1234" in page
    assert "/quarantine?omeka_ref=1234" in page


def test_item_not_in_catalog_has_no_catalog_actions() -> None:
    page = _page(_row(omeka_item_id=None, status="failed"), None)
    assert "Not in the catalog yet" in page
    assert "/visibility" not in page
    assert "/correct?" not in page
    assert "/quarantine?" not in page


def test_quarantined_item_shows_reason_not_quarantine_link() -> None:
    page = _page(_row(status="quarantine", block_reason="not_press_material"), False)
    assert "/quarantine?" not in page
    assert "not_press_material" in page
    assert "/visibility" not in page  # releasing a quarantine is an admin job


def test_problems_and_fields_are_escaped_and_formatted() -> None:
    page = _page(_row(error_message="<boom> & more"), True)
    assert "&lt;boom&gt; &amp; more" in page
    assert "<boom>" not in page
    assert "2026-11-06 12:34 UTC" in page
    assert "omeka_media_id" in page
    assert "&quot;a&quot;: 1" in page  # dict fields as JSON
    assert "—" in page  # None fields


def test_not_found_page_escapes_key() -> None:
    page = render_item_not_found("<x>", status_config={}).decode()
    assert "&lt;x&gt;" in page
    assert "<x>" not in page


# table rows

def test_row_links_point_to_item_page_without_mutating_actions() -> None:
    key_html, actions_html = row_links("a b", 5, admin_base=ADMIN, site_base=SITE)
    assert 'href="/item?key=a+b"' in key_html
    assert f"{ADMIN}/item/5/show" in actions_html
    for bad in ("/visibility", "/quarantine", "/correct", "<form"):
        assert bad not in actions_html
    assert row_links("a", None, admin_base=ADMIN, site_base=SITE)[1] == ""
