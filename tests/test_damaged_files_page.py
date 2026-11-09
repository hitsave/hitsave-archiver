"""Public "Damaged files" page on the archive, generated from the ledger's integrity results."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("dfp", ROOT / "scripts" / "publish-damaged-files-page.py")
dfp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dfp)


def _row(item_id, files, key="kit-01"):
    return {"game_key": key, "omeka_item_id": item_id, "inventory_json": {"files": files}}


ROWS = [
    _row(101, [
        {"path": "data/Some Kit/Scans/back.jpg", "size_bytes": 52_000,
         "format": {"puid": "fmt/43", "name": "JPEG File Interchange Format"},
         "integrity": {"status": "damaged", "check": "pillow", "detail": "image file is truncated (161 bytes not processed)"}},
        {"path": "data/Some Kit/Scans/front.jpg", "size_bytes": 99_000, "integrity": {"status": "ok"}},
        {"path": "data/Some Kit/Press <Release>.pdf", "size_bytes": 2_500_000,
         "integrity": {"status": "damaged", "check": "qpdf", "detail": "can't find startxref; unable to find trailer dictionary"}},
    ]),
    _row(202, [{"path": "data/X/a.jpg", "size_bytes": 1, "integrity": {"status": "unchecked", "detail": "password"}}], "x"),
    _row(None, [{"path": "data/Y/b.jpg", "size_bytes": 1, "integrity": {"status": "damaged", "detail": "x"}}], "no-item"),
    _row(303, [{"path": "data/Z/c.png", "size_bytes": 1, "integrity": {"status": "damaged", "detail": "x"}}], "private"),
]


def test_entries_only_damaged_files_of_public_items() -> None:
    public = {101: "Some Kit (Press Kit)", 303: None}
    items = dfp.damaged_entries(ROWS, item_title=lambda item_id: public.get(item_id))
    assert [i["item_id"] for i in items] == [101]
    files = items[0]["files"]
    assert [f["name"] for f in files] == ["Press <Release>.pdf", "back.jpg"]
    assert files[1]["folder"] == "Some Kit/Scans"
    assert files[1]["type"] == "JPEG File Interchange Format"
    assert files[0]["type"] == "PDF"  # no PRONOM name: from the extension


def test_plain_problem_wording() -> None:
    assert dfp.plain_problem("image file is truncated (5 bytes not processed)") == "Cut off: the end of the file is missing"
    assert dfp.plain_problem("can't find startxref; unable to find trailer dictionary") == "Cut off: the end of the file is missing"
    assert dfp.plain_problem("moov atom not found") == "Cut off: the end of the file is missing"
    assert dfp.plain_problem("not a readable image (unknown or corrupt header)") == "Not a readable file of this type"
    assert dfp.plain_problem("broken data stream when reading image file") == "Damaged data inside the file"
    assert dfp.plain_problem("TIFFFillStrip: Read error on strip 24; got 63830 bytes, expected 64026.") == "Cut off: the end of the file is missing"
    assert dfp.plain_problem("Not a TIFF or MDI file, bad magic number 28526 (0x6f6e)") == "Not a readable file of this type"
    assert dfp.plain_problem("ZIPDecode: Decoding error at scanline 252") == "Damaged data inside the file"


def test_html_escapes_names_links_items_and_says_how_to_help() -> None:
    items = dfp.damaged_entries(ROWS, item_title=lambda i: {101: "Kit & <Co>"}.get(i))
    html = dfp.render_html(items, updated="2026-11-09", site_slug="start", contact_html='<a href="https://x">us</a>')
    assert "Press &lt;Release&gt;.pdf" in html and "<Release>" not in html
    assert "Kit &amp; &lt;Co&gt;" in html
    assert 'href="/s/start/item/101"' in html
    assert "2 damaged files in 1 item" in html
    assert '<a href="https://x">us</a>' in html
    assert "2026-11-09" in html
    assert "52 KB" in html and "2.5 MB" in html


def test_empty_page_says_so() -> None:
    html = dfp.render_html([], updated="2026-11-09", site_slug="start", contact_html="us")
    assert "No damaged files" in html


def test_page_body_has_title_and_html_blocks() -> None:
    body = dfp.page_body(site_id=2, slug="damaged-files", title="Damaged files", html="<p>x</p>")
    assert body["o:slug"] == "damaged-files" and body["o:title"] == "Damaged files"
    assert body["o:site"] == {"o:id": 2}
    assert [b["o:layout"] for b in body["o:block"]] == ["pageTitle", "html"]
    assert body["o:block"][1]["o:data"] == {"html": "<p>x</p>"}
