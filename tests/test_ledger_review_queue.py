"""Review queue: hidden Omeka items, the queue order, and the one-package-at-a-time page."""
from __future__ import annotations

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from omeka_status import fetch_private_item_ids  # noqa: E402
from review_queue import (  # noqa: E402
    HiddenItems,
    next_in_queue,
    parse_queue_args,
    queue_counts,
    render_review_empty,
    render_review_page,
    render_review_unavailable,
)

ADMIN = "https://archive.hitsave.org/admin"


# --- fetch_private_item_ids -------------------------------------------------------------------

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


def test_private_ids_pages_until_empty(tmp_path) -> None:
    pages = {1: [{"o:id": 1}, {"o:id": 2}], 2: [{"o:id": 3}], 3: []}
    seen = []

    def get(url, params=None, **kw):
        seen.append(params)
        return _Resp(200, pages[params["page"]])

    assert fetch_private_item_ids(get=get, config_root=_config(tmp_path), per_page=2) == {1, 2, 3}
    assert seen[0]["is_public"] == "0" and seen[0]["key_identity"] == "id"


def test_private_ids_none_on_error(tmp_path) -> None:
    root = _config(tmp_path)
    assert fetch_private_item_ids(get=lambda u, **k: _Resp(500, {}), config_root=root) is None
    assert fetch_private_item_ids(get=lambda u, **k: _Resp(200, {"x": 1}), config_root=root) is None


# --- HiddenItems ------------------------------------------------------------------------------

class _Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_hidden_items_caches_refreshes_and_keeps_last_good() -> None:
    clock = _Clock()
    results = [{1, 2}, None, {3}]
    calls = []

    def fetch():
        calls.append(1)
        return results[len(calls) - 1]

    hidden = HiddenItems(fetch, ttl=600, clock=clock)
    assert hidden.ids() == {1, 2}
    clock.t = 100
    assert hidden.ids() == {1, 2} and len(calls) == 1  # cached
    hidden.mark_public(1)
    assert hidden.ids() == {2}
    hidden.mark_private(9)
    assert hidden.ids() == {2, 9}
    clock.t = 700
    assert hidden.ids() == {2, 9} and len(calls) == 2  # refresh failed: keep last good
    clock.t = 1400
    assert hidden.ids() == {3}


def test_hidden_items_none_when_never_loaded() -> None:
    assert HiddenItems(lambda: None).ids() is None


# --- queries ----------------------------------------------------------------------------------

class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self._rows = []

    def execute(self, sql, args):
        self.conn.queries.append((sql, args))
        self._rows, self.description = self.conn.answer(sql, args)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, answer):
        self.answer = answer
        self.queries = []

    def cursor(self):
        return _Cur(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_queue_counts_by_moby_status() -> None:
    conn = _Conn(lambda sql, args: ([("ok", 3), ("no_match", 2), (None, 1)], [("m",), ("n",)]))
    counts = queue_counts(lambda: conn, {1, 2})
    assert counts == {"ok": 3, "no_match": 2, "(none)": 1, "": 6}
    sql, args = conn.queries[0]
    assert "status = 'complete'" in sql and "omeka_item_id = ANY(%s)" in sql
    assert sorted(args[0]) == [1, 2]


def test_next_in_queue_filters_and_wraps() -> None:
    def answer(sql, args):
        after = args[-1]
        rows = [(5, "k5", 105)] if after < 5 else []
        return rows, [("id",), ("game_key",), ("omeka_item_id",)]

    conn = _Conn(answer)
    assert next_in_queue(lambda: conn, {105}, moby="ok", after=0) == {"id": 5, "game_key": "k5", "omeka_item_id": 105}
    sql, args = conn.queries[0]
    assert "moby_status = %s" in sql and args[1] == "ok"
    conn.queries.clear()
    assert next_in_queue(lambda: conn, {105}, after=5)["id"] == 5  # wrapped to the start
    assert [a[-1] for _, a in conn.queries] == [5, 0]
    assert next_in_queue(lambda: conn, set(), after=0) is None


def test_parse_queue_args() -> None:
    assert parse_queue_args({"moby": ["ok"], "after": ["12"]}) == ("ok", 12)
    assert parse_queue_args({"moby": ["bogus"], "after": ["x"]}) == ("", 0)
    assert parse_queue_args({}) == ("", 0)


# --- render -----------------------------------------------------------------------------------

ROW = {"id": 12, "game_key": "press-foo bar", "omeka_item_id": 345, "moby_status": "ok", "moby_title": "Foo <Bar>",
       "status": "complete", "updated_at": None}
ITEM = {
    "o:title": "Foo <Bar>",
    "dcterms:title": [{"@value": "Foo <Bar>"}],
    "dcterms:description": [{"@value": "Line one.\nLine two."}],
    "dcterms:creator": [{"@value": "Dev A"}],
    "bibo:uri": [{"@id": "https://www.mobygames.com/game/1/foo/"}],
}
COUNTS = {"ok": 3, "ambiguous": 1, "": 4}


def _page(**over):
    kw = dict(row=ROW, item=ITEM, counts=COUNTS, moby="ok", status_config={}, admin_base=ADMIN)
    kw.update(over)
    return render_review_page(**kw).decode()


def test_review_page_shows_record_and_escapes() -> None:
    page = _page()
    assert "Foo &lt;Bar&gt;" in page and "<Bar>" not in page
    assert "Line one.\nLine two." in page
    assert "Dev A" in page
    assert "https://www.mobygames.com/game/1/foo/" in page
    assert f"{ADMIN}/item/345/show" in page
    assert "/item?key=press-foo+bar" in page
    assert "3 left" in page


def test_review_page_actions() -> None:
    page = _page()
    assert 'action="/review/publish"' in page
    assert 'name="omeka_ref" value="345"' in page and 'name="after" value="12"' in page and 'name="moby" value="ok"' in page
    assert "Publish and next" in page and "data-busy" in page
    assert 'href="/review?moby=ok&amp;after=12"' in page  # skip
    from urllib.parse import urlencode

    query = urlencode({"omeka_ref": "345", "return_to": "/review?moby=ok&after=11"})  # comes back to this item
    assert html.escape(f"/correct?{query}") in page
    assert html.escape(f"/quarantine?{query}") in page


def test_review_page_moby_chips_link_and_highlight() -> None:
    page = _page(moby="ambiguous")
    assert 'href="/review?moby=ok"' in page and 'href="/review"' in page
    assert 'class="chip chip-active" href="/review?moby=ambiguous"' in page


def test_review_page_without_omeka_item_still_renders() -> None:
    page = _page(item=None)
    assert "couldn't read" in html.unescape(page).lower()
    assert "Publish and next" in page


def test_empty_and_unavailable_pages() -> None:
    assert "Nothing left to review" in render_review_empty(counts={"": 0}, moby="", status_config={}).decode()
    page = html.unescape(render_review_unavailable(status_config={}).decode())
    assert "Couldn't get the list of hidden items from Omeka" in page


def test_review_page_warns_about_damaged_files_before_publishing() -> None:
    row = dict(ROW, inventory_json={"files": [
        {"path": "data/Foo/shot.jpg", "size_bytes": 9,
         "integrity": {"status": "damaged", "check": "pillow", "detail": "not a readable image (unknown or corrupt header)"}},
    ]})
    page = html.unescape(_page(row=row))
    assert "Damaged files (1)" in page and "shot.jpg" in page
    assert "Not a readable file of this type" in page
    assert "once this record is published" in page
    assert page.index("Damaged files (1)") < page.index("Publish and next")


def test_review_page_has_no_damaged_notice_without_damaged_files() -> None:
    assert "Damaged files" not in _page()
