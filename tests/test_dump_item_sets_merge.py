"""Dump registration owns ONE item set (first); sets added by admins (e.g. Famitsu Wave) are kept."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from omeka_catalog_common import apply_item_item_sets, merged_item_sets  # noqa: E402


def test_automated_set_first_admin_sets_kept() -> None:
    assert merged_item_sets(9001, [511]) == [9001, 511]


def test_no_duplicates_and_order_kept() -> None:
    assert merged_item_sets(9001, [511, 9001, 6082]) == [9001, 511, 6082]
    assert merged_item_sets(9001, []) == [9001]


class _Resp:
    def __init__(self, body=None):
        self.ok, self.status_code, self._body, self.text = True, 200, body, ""

    def json(self):
        return self._body


class _Session:
    def __init__(self, item):
        self.item, self.put_body = item, None

    def get(self, url, **kw):
        return _Resp(self.item)

    def put(self, url, json=None, **kw):
        self.put_body = json
        return _Resp(json)


class _Api:
    @staticmethod
    def api_url(base, path, creds):
        return f"{base}/api/{path}"


def test_apply_keeps_existing_sets_and_whole_item() -> None:
    item = {"@id": "x", "o:id": 5, "o:title": "Famitsu Wave DVD", "o:item_set": [{"o:id": 511}],
            "dcterms:title": [{"@value": "Famitsu Wave DVD"}]}
    s = _Session(item)
    apply_item_item_sets(s, "https://o", {}, 5, [9001], api=_Api)
    assert s.put_body["o:item_set"] == [{"o:id": 9001}, {"o:id": 511}]
    assert s.put_body["dcterms:title"] == item["dcterms:title"]  # full item, nothing else dropped
    assert "@id" not in s.put_body
