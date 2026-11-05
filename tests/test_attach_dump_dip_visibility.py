"""Dump gallery media follows the item's visibility."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pytest.importorskip("psycopg")
import attach_dump_external_dip_omeka as m  # noqa: E402


class _Resp:
    def __init__(self, body, ok=True):
        self._body, self.ok, self.status_code, self.text = body, ok, 200 if ok else 404, ""

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("http")


class _Session:
    def __init__(self, bodies):
        self.bodies, self.patches = bodies, []

    def get(self, url, **_):
        return _Resp(self.bodies[url]) if url in self.bodies else _Resp({}, ok=False)

    def patch(self, url, json, **_):
        self.patches.append((url, json))
        return _Resp({})


def _url(base, path, creds):
    return f"{base}/{path}"


def test_item_visibility() -> None:
    s = _Session({"b/items/1": {"o:is_public": True}})
    assert m._item_is_public(s, "b", {}, 1, api_url=_url) is True
    assert m._item_is_public(s, "b", {}, 2, api_url=_url) is None


def test_media_made_public_only_when_different() -> None:
    s = _Session({"b/media/5": {"o:is_public": False}, "b/media/6": {"o:is_public": True}})
    m._set_media_visibility(s, "b", {}, 5, True, api_url=_url)
    m._set_media_visibility(s, "b", {}, 6, True, api_url=_url)
    assert s.patches == [("b/media/5", {"o:is_public": True})]
