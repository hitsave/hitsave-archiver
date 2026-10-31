"""Wasabi MOVE/delete paths must never lose objects on partial failure."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import wasabi_rename

ROOT = Path(__file__).resolve().parents[1]


class _Paginator:
    def __init__(self, s3):
        self.s3 = s3

    def paginate(self, Bucket, Prefix, Delimiter=None):
        yield self.s3.list_objects_v2(Bucket=Bucket, Prefix=Prefix, Delimiter=Delimiter, MaxKeys=10**9)


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)
        self.fail_copy: set[str] = set()

    def get_paginator(self, _name):
        return _Paginator(self)

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000, Delimiter=None, **_):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if not Delimiter:
            keys = keys[:MaxKeys]
            return {"Contents": [{"Key": k, "Size": len(self.objects[k])} for k in keys], "IsTruncated": False}
        contents, prefixes = [], set()
        for k in keys:
            rest = k[len(Prefix):]
            if Delimiter in rest:
                prefixes.add(Prefix + rest.split(Delimiter, 1)[0] + Delimiter)
            else:
                contents.append({"Key": k, "Size": len(self.objects[k])})
        return {"Contents": contents, "CommonPrefixes": [{"Prefix": p} for p in sorted(prefixes)], "IsTruncated": False}

    def head_object(self, Bucket, Key):
        data = self.objects[Key]
        return {"ContentLength": len(data), "ETag": f'"{hash(data)}"'}

    def copy_object(self, Bucket, Key, CopySource):
        src = CopySource["Key"]
        self.objects[Key] = b"" if src in self.fail_copy else self.objects[src]

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)


def fake_move(s3: FakeS3, *, skip: set[str] = frozenset(), errors: list[str] | None = None):
    """Simulated Wasabi MOVE that silently leaves `skip` keys behind."""

    def _move(client, *, bucket, source_prefix, destination_prefix, overwrite=False, quiet=True):
        src = source_prefix.strip("/") + "/"
        dst = destination_prefix.strip("/") + "/"
        moved = 0
        for key in [k for k in list(s3.objects) if k.startswith(src)]:
            if key in skip:
                continue
            s3.objects[dst + key[len(src):]] = s3.objects.pop(key)
            moved += 1
        return {"move_count": moved, "errors": list(errors or [])}

    return _move


def test_verified_move_success(monkeypatch: pytest.MonkeyPatch) -> None:
    s3 = FakeS3({"a/x.iso": b"iso", "a/m/y.txt": b"t"})
    monkeypatch.setattr(wasabi_rename, "wasabi_move_prefix", fake_move(s3))
    res = wasabi_rename.wasabi_move_prefix_verified(s3, bucket="b", source_prefix="a", destination_prefix="b2")
    assert res["verified_objects"] == 2
    assert set(s3.objects) == {"b2/x.iso", "b2/m/y.txt"}


def test_verified_move_detects_silent_partial_move(monkeypatch: pytest.MonkeyPatch) -> None:
    s3 = FakeS3({"a/x.iso": b"iso", "a/y.txt": b"t"})
    monkeypatch.setattr(wasabi_rename, "wasabi_move_prefix", fake_move(s3, skip={"a/y.txt"}))
    with pytest.raises(wasabi_rename.WasabiMoveIncomplete, match="still at source"):
        wasabi_rename.wasabi_move_prefix_verified(s3, bucket="b", source_prefix="a", destination_prefix="b2")
    assert s3.objects["a/y.txt"] == b"t"  # nothing deleted


def test_verified_move_surfaces_reported_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    s3 = FakeS3({"a/x.iso": b"iso"})
    monkeypatch.setattr(wasabi_rename, "wasabi_move_prefix", fake_move(s3, errors=["a/x.iso: InternalError"]))
    with pytest.raises(wasabi_rename.WasabiMoveIncomplete, match="InternalError"):
        wasabi_rename.wasabi_move_prefix_verified(s3, bucket="b", source_prefix="a", destination_prefix="b2")


def test_verified_move_refuses_non_empty_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    s3 = FakeS3({"a/x.iso": b"iso", "b2/old.iso": b"old"})
    monkeypatch.setattr(wasabi_rename, "wasabi_move_prefix", fake_move(s3))
    with pytest.raises(wasabi_rename.WasabiMoveIncomplete, match="already has objects"):
        wasabi_rename.wasabi_move_prefix_verified(s3, bucket="b", source_prefix="a", destination_prefix="b2")
    assert "a/x.iso" in s3.objects


def _load_flatten(monkeypatch: pytest.MonkeyPatch):
    spec = importlib.util.spec_from_file_location(
        "flatten", ROOT / "scripts" / "remote-intake" / "flatten-gamewavedvd-wasabi.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GW = "GameWaveDVD"


def _wrapper_bucket() -> dict[str, bytes]:
    return {
        f"{GW}/Vol 1/": b"",
        f"{GW}/Vol 1/cover.jpg": b"loose",
        f"{GW}/Vol 1/G_WAVE_01/disc.iso": b"iso",
        f"{GW}/Vol 1/G_WAVE_01/!submissionInfo.txt": b"info",
    }


def test_flatten_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    flatten = _load_flatten(monkeypatch)
    s3 = FakeS3(_wrapper_bucket())
    monkeypatch.setattr(wasabi_rename, "wasabi_move_prefix", fake_move(s3))
    stats = flatten.run(s3, "b", dry_run=False, overwrite=False)
    assert stats["errors"] == 0 and stats["hoisted"] == 1
    assert set(s3.objects) == {f"{GW}/G_WAVE_01/disc.iso", f"{GW}/G_WAVE_01/!submissionInfo.txt", f"{GW}/G_WAVE_01/cover.jpg"}


def test_flatten_partial_move_never_deletes_originals(monkeypatch: pytest.MonkeyPatch) -> None:
    flatten = _load_flatten(monkeypatch)
    s3 = FakeS3(_wrapper_bucket())
    left = f"{GW}/Vol 1/G_WAVE_01/disc.iso"
    monkeypatch.setattr(wasabi_rename, "wasabi_move_prefix", fake_move(s3, skip={left}))
    stats = flatten.run(s3, "b", dry_run=False, overwrite=False)
    assert stats["errors"] >= 1 and stats["hoisted"] == 0
    assert s3.objects[left] == b"iso"  # the old code deleted this with the outer prefix


def test_flatten_failed_loose_copy_keeps_source(monkeypatch: pytest.MonkeyPatch) -> None:
    flatten = _load_flatten(monkeypatch)
    s3 = FakeS3(_wrapper_bucket())
    s3.fail_copy.add(f"{GW}/Vol 1/cover.jpg")
    monkeypatch.setattr(wasabi_rename, "wasabi_move_prefix", fake_move(s3))
    stats = flatten.run(s3, "b", dry_run=False, overwrite=False)
    assert stats["errors"] >= 1 and stats["hoisted"] == 0
    assert s3.objects[f"{GW}/Vol 1/cover.jpg"] == b"loose"
    assert f"{GW}/Vol 1/G_WAVE_01/disc.iso" in s3.objects  # stopped before the prefix move
