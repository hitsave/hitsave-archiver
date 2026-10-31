"""Upload-time ETag verification, stale-object detection and Wasabi-side fixity."""
from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path

import pytest

import s3_etag
import upload_bag_wasabi

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("wasabi_fixity", ROOT / "scripts" / "wasabi-fixity-check.py")
wasabi_fixity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wasabi_fixity)


def _etag_for(data: bytes, part: int) -> str:
    if len(data) < part:
        return hashlib.md5(data).hexdigest()
    parts = [hashlib.md5(data[i : i + part]).digest() for i in range(0, len(data), part)]
    return f"{hashlib.md5(b''.join(parts)).hexdigest()}-{len(parts)}"


# --- s3_etag ---------------------------------------------------------------------------

@pytest.mark.parametrize("size", [0, 5, 16, 17, 48, 50])
def test_hash_file_matches_s3_etag_rules(tmp_path: Path, size: int) -> None:
    data = bytes(range(256))[:size]
    p = tmp_path / "f"
    p.write_bytes(data)
    h = s3_etag.hash_file(p, part_size=16)
    assert h.sha256 == hashlib.sha256(data).hexdigest()
    assert h.expected_etag() == _etag_for(data, 16)
    assert s3_etag.etag_matches(h, f'"{_etag_for(data, 16)}"')[0]


def test_etag_mismatch_and_part_count(tmp_path: Path) -> None:
    p = tmp_path / "f"
    p.write_bytes(b"a" * 40)
    h = s3_etag.hash_file(p, part_size=16)
    assert not s3_etag.etag_matches(h, "0" * 32 + "-3")[0]
    ok, detail = s3_etag.etag_matches(h, "0" * 32 + "-2")
    assert not ok and "parts" in detail


# --- upload verification ---------------------------------------------------------------

class FakeS3:
    def __init__(self, part: int):
        self.objects: dict[str, bytes] = {}
        self.part = part
        self.corrupt_next = False

    def upload_file(self, path, bucket, key, Config=None):
        data = Path(path).read_bytes()
        if self.corrupt_next:
            data = data[:-1] + bytes([data[-1] ^ 1])
            self.corrupt_next = False
        self.objects[key] = data

    def head_object(self, Bucket, Key):
        data = self.objects[Key]
        return {"ContentLength": len(data), "ETag": f'"{_etag_for(data, self.part)}"'}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def get_paginator(self, _):
        s3 = self

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k, "Size": len(v)} for k, v in s3.objects.items() if k.startswith(Prefix)]}

        return P()


def test_verify_detects_large_file_corruption_without_download(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(upload_bag_wasabi, "VERIFY_DOWNLOAD_MAX_BYTES", 0)  # force HEAD-only path
    p = tmp_path / "disc.iso"
    p.write_bytes(b"x" * 100)
    s3 = FakeS3(part=16)
    s3.corrupt_next = True
    s3.upload_file(p, "b", "k")
    h = s3_etag.hash_file(p, part_size=16)
    with pytest.raises(RuntimeError, match="ETag"):
        upload_bag_wasabi.verify_uploaded_object(s3, bucket="b", key="k", size=100, local_digest=h.sha256, local_hashes=h)
    s3.upload_file(p, "b", "k")
    upload_bag_wasabi.verify_uploaded_object(s3, bucket="b", key="k", size=100, local_digest=h.sha256, local_hashes=h)


def _bag(tmp_path: Path) -> Path:
    bag = tmp_path / "bag"
    (bag / "data").mkdir(parents=True)
    (bag / "bagit.txt").write_text("BagIt-Version: 1.0\n")
    (bag / "data" / "a.bin").write_bytes(b"a" * 70)
    return bag


def _patch_upload_env(monkeypatch, s3: FakeS3) -> None:
    monkeypatch.setattr(upload_bag_wasabi, "load_wasabi_settings", lambda p: ({"enabled": True, "verify_upload": True, "credentials_file": "x"}, None))
    monkeypatch.setattr(upload_bag_wasabi, "load_yaml", lambda p: {})
    monkeypatch.setattr(upload_bag_wasabi, "make_s3_client", lambda w, c: s3)
    monkeypatch.setattr(upload_bag_wasabi, "hash_file", lambda p: s3_etag.hash_file(p, part_size=16))


def test_upload_reports_stale_objects_from_earlier_bag(tmp_path: Path, monkeypatch) -> None:
    s3 = FakeS3(part=16)
    _patch_upload_env(monkeypatch, s3)
    s3.objects["dumps/X/pkg/data/old-extra.bin"] = b"stale"
    with pytest.raises(RuntimeError, match="not part of this bag"):
        upload_bag_wasabi.upload_bag_to_prefix(_bag(tmp_path), "dumps/X/pkg", bucket="b", ingest_kind="dump")
    assert "dumps/X/pkg/data/old-extra.bin" in s3.objects  # reported, never deleted


def test_upload_clean_prefix_passes(tmp_path: Path, monkeypatch) -> None:
    s3 = FakeS3(part=16)
    _patch_upload_env(monkeypatch, s3)
    out = upload_bag_wasabi.upload_bag_to_prefix(_bag(tmp_path), "dumps/X/pkg", bucket="b", ingest_kind="dump")
    assert out["package_storage_prefix"] == "dumps/X/pkg"
    assert set(s3.objects) == {"dumps/X/pkg/bagit.txt", "dumps/X/pkg/data/a.bin"}


# --- Wasabi-side fixity ----------------------------------------------------------------

class _Body(io.BytesIO):
    def iter_chunks(self, n):
        while chunk := self.read(n):
            yield chunk


class FixityS3(FakeS3):
    def get_object(self, Bucket, Key):
        return {"Body": _Body(self.objects[Key])}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _wasabi_bag(payload: bytes = b"iso") -> tuple[FixityS3, dict]:
    s3 = FixityS3(part=16)
    r = "dumps/X/pkg/"
    s3.objects[r + "data/disc.iso"] = payload
    s3.objects[r + "bagit.txt"] = b"BagIt-Version: 1.0\n"
    s3.objects[r + "manifest-sha256.txt"] = f"{_sha(payload)}  data/disc.iso\n".encode()
    s3.objects[r + "tagmanifest-sha256.txt"] = (
        f"{_sha(b'BagIt-Version: 1.0' + bytes([10]))}  bagit.txt\n"
        f"{_sha(s3.objects[r + 'manifest-sha256.txt'])}  manifest-sha256.txt\n"
    ).encode()
    inventory = {"files": [{"path": "data/disc.iso", "sha256": _sha(b"iso")}]}
    return s3, inventory


def test_fixity_pass() -> None:
    s3, inv = _wasabi_bag()
    problems, checked, read = wasabi_fixity.check_package(s3, "b", "dumps/X/pkg", inv)
    assert problems == [] and checked == 3


def test_fixity_detects_bitrot() -> None:
    s3, inv = _wasabi_bag()
    s3.objects["dumps/X/pkg/data/disc.iso"] = b"isX"
    problems, _, _ = wasabi_fixity.check_package(s3, "b", "dumps/X/pkg", inv)
    assert any("SHA-256 mismatch: data/disc.iso" in p for p in problems)


def test_fixity_detects_manifest_regenerated_over_changed_bytes() -> None:
    s3, inv = _wasabi_bag(payload=b"changed")  # manifest consistent with new bytes, not ingest
    problems, _, _ = wasabi_fixity.check_package(s3, "b", "dumps/X/pkg", inv)
    assert any("differs from ingest inventory" in p for p in problems)


def test_fixity_detects_stray_and_missing() -> None:
    s3, inv = _wasabi_bag()
    s3.objects["dumps/X/pkg/data/extra.bin"] = b"x"
    del s3.objects["dumps/X/pkg/bagit.txt"]
    problems, _, _ = wasabi_fixity.check_package(s3, "b", "dumps/X/pkg", inv)
    assert any("stray payload object: data/extra.bin" in p for p in problems)
    assert any("missing object: bagit.txt" in p for p in problems)
