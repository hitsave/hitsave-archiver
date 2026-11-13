"""Dump registration owns ONE item set (first); sets added by admins (e.g. Famitsu Wave) are kept."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from omeka_catalog_common import apply_item_item_sets  # noqa: E402


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


def test_resolve_item_set_id_never_falls_back_to_first_set() -> None:
    """Omeka ignored ?title= and the old code returned rows[0] (Nintendo Power) for unknown titles."""
    import pytest
    from omeka_catalog_common import load_register_dip_omeka_api

    reg = load_register_dip_omeka_api()
    calls = []

    class S:
        def get(self, url, params=None, **kw):
            calls.append(params)
            r = _Resp([{"o:id": 492, "o:title": "Nintendo Power"}])
            r.raise_for_status = lambda: None
            return r

    with pytest.raises(RuntimeError):
        reg.resolve_item_set_id(S(), "https://o", {"key_identity": "i", "key_credential": "c"}, "Coverdiscs")
    assert calls[0]["property[0][text]"] == "Coverdiscs" and calls[0]["property[0][type]"] == "eq"
