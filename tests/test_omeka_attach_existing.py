"""Bag-only registration onto an existing Omeka item (attach_to_omeka_item)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import omeka_attach_existing as m  # noqa: E402

DIP = "omeka_dip_package"


def _flat(mid: int, public: bool = True) -> dict:
    return {"o:id": mid, "o:ingester": "upload", "o:renderer": "file", "o:is_public": public, "data": []}


def _pkg(mid: int, prefix: str = "press-material/B/bmx-xxx-01a") -> dict:
    return {
        "o:id": mid,
        "o:ingester": DIP,
        "o:renderer": DIP,
        "o:is_public": True,
        "data": {"dip_storage": "wasabi-s3", "package_storage_prefix": prefix},
    }


class _Resp:
    def __init__(self, body, ok=True):
        self._body, self.ok = body, ok
        self.status_code, self.text = (200 if ok else 404), ""

    def json(self):
        return self._body


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


@pytest.mark.parametrize("raw,want", [(None, None), ("", None), (17734, 17734), ("16481", 16481)])
def test_attach_item_id(raw, want) -> None:
    assert m.attach_item_id({"attach_to_omeka_item": raw}) == want
    assert m.attach_item_id({}) is None


@pytest.mark.parametrize("raw", [True, "abc", 0, -3])
def test_attach_item_id_rejects_bad_values(raw) -> None:
    with pytest.raises(SystemExit):
        m.attach_item_id({"attach_to_omeka_item": raw})


def test_missing_item_stops() -> None:
    with pytest.raises(SystemExit):
        m.get_item(_Session({}), "b", {}, 5, api_url=_url)


def test_item_media_in_item_order() -> None:
    s = _Session({"b/media/2": _flat(2), "b/media/1": _flat(1)})
    got = m.item_media(s, "b", {}, {"o:media": [{"o:id": 2}, {"o:id": 1}]}, api_url=_url)
    assert [b["o:id"] for b in got] == [2, 1]


def test_existing_package_is_found_but_disc_contents_is_not() -> None:
    media = [_flat(1), _pkg(9, prefix="disc-contents/x"), _pkg(7)]
    assert m.existing_package_id(media, dip_ingester=DIP) == 7
    assert m.existing_package_id([_flat(1)], dip_ingester=DIP) is None


def test_rerun_with_our_package_is_ok() -> None:
    assert m.check_existing_package(None, None, 5) is False
    assert m.check_existing_package(7, 7, 5) is True


@pytest.mark.parametrize("ledger_media", [None, 8])
def test_someone_elses_package_stops(ledger_media) -> None:
    with pytest.raises(SystemExit):
        m.check_existing_package(7, ledger_media, 5)


def test_only_public_flat_media_are_hidden() -> None:
    media = [_flat(1), _flat(2, public=False), _pkg(7), _pkg(9, prefix="disc-contents/x"), _flat(3)]
    hide = m.flat_media_to_hide(media, keep_media_id=7, dip_ingester=DIP)
    assert [b["o:id"] for b in hide] == [1, 3]


def test_hide_sends_only_visibility() -> None:
    s = _Session({})
    m.hide_media(s, "b", {}, 4, api_url=_url)
    assert s.patches == [("b/media/4", {"o:is_public": False})]
