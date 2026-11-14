"""Copying one package from Wasabi to S3 Glacier Deep Archive (scripts/glacier_copy_core.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from glacier_copy_core import TargetObject as T  # noqa: E402
from glacier_copy_core import copy_file, copy_package, expected_digests, plan_package  # noqa: E402
from glacier_fakes import FakeS3, client_error, crc64nvme_b64, sha256_of  # noqa: E402

DATA = b"0123456789abcdef" * 4
BIG = bytes(range(256)) * (12 * 2**20 // 256)  # 12 MiB
KEY = "dumps/a/manifest-sha256.txt"
DST_KEY = "hitsave-dumps/" + KEY


@pytest.fixture
def src():
    s = FakeS3()
    s.put(KEY, DATA)
    s.put("dumps/a/data/disc.iso", BIG)
    return s


@pytest.fixture
def dst():
    return FakeS3()


def test_small_file_uploads_with_sha256_metadata_and_deep_archive(src, dst):
    r = copy_file(src, "hitsave-dumps", KEY, dst, "copy", DST_KEY, sha256_of(DATA))
    obj = dst.objects[DST_KEY]
    assert r.ok and r.bytes == len(DATA)
    assert obj.storage_class == "DEEP_ARCHIVE" and obj.metadata["sha256"] == sha256_of(DATA)
    assert r.crc64nvme == crc64nvme_b64(DATA)


def test_large_file_sends_full_object_crc64nvme(src, dst):
    r = copy_file(src, "hitsave-dumps", "dumps/a/data/disc.iso", dst, "copy", "k", sha256_of(BIG),
                  part_size=5 * 2**20)
    done = dst.completed[-1]
    assert r.ok and done["Parts"] == [1, 2, 3]
    assert done["ChecksumType"] == "FULL_OBJECT"
    assert done["ChecksumCRC64NVME"] == crc64nvme_b64(BIG)
    assert dst.objects["k"].storage_class == "DEEP_ARCHIVE"
    assert dst.objects["k"].metadata["sha256"] == sha256_of(BIG)


def test_wrong_sha256_aborts_the_upload_and_reports_damage(src, dst):
    r = copy_file(src, "hitsave-dumps", "dumps/a/data/disc.iso", dst, "copy", "k", "0" * 64,
                  part_size=5 * 2**20)
    assert not r.ok and "differs from the bag manifest" in r.problem
    assert dst.aborted == ["k"] and "k" not in dst.objects


def test_wrong_sha256_on_a_small_file_uploads_nothing(src, dst):
    r = copy_file(src, "hitsave-dumps", KEY, dst, "copy", DST_KEY, "0" * 64)
    assert not r.ok and dst.puts == []


def test_file_without_expected_digest_is_copied_and_recorded(src, dst):
    r = copy_file(src, "hitsave-dumps", KEY, dst, "copy", DST_KEY, None)
    assert r.ok and dst.objects[DST_KEY].metadata["sha256"] == sha256_of(DATA)


def test_source_error_mid_upload_aborts_and_raises(src, dst):
    def broken(key):
        raise client_error("InternalError", "GetObject", 500)
    src.on_get = broken
    with pytest.raises(Exception):
        copy_file(src, "hitsave-dumps", KEY, dst, "copy", DST_KEY, sha256_of(DATA))
    assert dst.puts == []


def test_plan_skips_unchanged_uploads_changed_and_marks_removed():
    sha_a, sha_b, sha_x = "a" * 64, "b" * 64, "c" * 64
    p = plan_package(source={"a": 3, "b": 5}, target={"a": T(3, sha_a), "old": T(9, sha_x)},
                     expected={"a": sha_a, "b": sha_b})
    assert p.unchanged == ["a"] and p.upload == ["b"] and p.delete_marker == ["old"]


def test_plan_uploads_a_file_whose_size_or_digest_changed():
    target = {"a": T(3, "x" * 64), "b": T(5, "b" * 64), "c": T(1, "c" * 64)}
    p = plan_package(source={"a": 3, "b": 4, "c": 1}, target=target, expected={"a": "a" * 64, "b": "b" * 64})
    assert p.upload == ["a", "b", "c"] and p.unchanged == []


def _bag(s: FakeS3, root: str, files: dict[str, bytes]) -> None:
    manifest = "".join(f"{sha256_of(v)}  {k}\n" for k, v in files.items() if k.startswith("data/"))
    s.put(root + "manifest-sha256.txt", manifest.encode())
    tags = {k: v for k, v in files.items() if not k.startswith("data/")}
    tags["manifest-sha256.txt"] = manifest.encode()
    for k, v in files.items():
        s.put(root + k, v)
    s.put(root + "tagmanifest-sha256.txt", "".join(f"{sha256_of(v)} {k}\n" for k, v in tags.items()).encode())


def test_expected_digests_include_the_tag_manifest_itself():
    s = FakeS3()
    _bag(s, "dumps/a/", {"data/disc.iso": DATA, "bagit.txt": b"BagIt-Version: 1.0\n"})
    expected, tagmanifest = expected_digests(s, "b", "dumps/a")
    assert expected["data/disc.iso"] == sha256_of(DATA)
    assert expected["tagmanifest-sha256.txt"] == sha256_of(tagmanifest)
    assert set(expected) == {"data/disc.iso", "bagit.txt", "manifest-sha256.txt", "tagmanifest-sha256.txt"}


def test_copy_package_copies_then_only_changes_then_marks_removed_files():
    src, dst = FakeS3(), FakeS3()
    _bag(src, "dumps/a/", {"data/old name.iso": DATA, "bagit.txt": b"BagIt-Version: 1.0\n"})
    first = copy_package(src, "hitsave-dumps", "dumps/a", dst, "copy")
    assert first.ok and first.files_uploaded == 4 and first.delete_markers == 0
    assert "hitsave-dumps/dumps/a/data/old name.iso" in dst.objects

    # Rename the payload file (as #121 did) and rewrite the manifests.
    for k in list(src.objects):
        src.delete_object(Bucket="b", Key=k)
    _bag(src, "dumps/a/", {"data/new name.iso": DATA, "bagit.txt": b"BagIt-Version: 1.0\n"})
    second = copy_package(src, "hitsave-dumps", "dumps/a", dst, "copy")
    assert second.ok
    assert second.files_unchanged == 1  # bagit.txt
    assert second.files_uploaded == 3   # new payload name and both manifests
    assert second.delete_markers == 1
    current = {k for k in dst.objects if k.startswith("hitsave-dumps/dumps/a/")}
    assert current == {"hitsave-dumps/dumps/a/" + k for k in
                       ("data/new name.iso", "bagit.txt", "manifest-sha256.txt", "tagmanifest-sha256.txt")}
    # The old version is still stored.
    assert dst.versions["hitsave-dumps/dumps/a/data/old name.iso"][0].data == DATA


def test_copy_package_reports_damaged_files_and_keeps_going():
    src, dst = FakeS3(), FakeS3()
    _bag(src, "dumps/a/", {"data/one.bin": DATA, "data/two.bin": DATA + b"!"})
    src.put("dumps/a/data/one.bin", b"damaged")
    r = copy_package(src, "hitsave-dumps", "dumps/a", dst, "copy")
    assert not r.ok
    assert r.problems == ["SHA-256 of data/one.bin differs from the bag manifest"]
    assert "hitsave-dumps/dumps/a/data/two.bin" in dst.objects


def test_large_file_without_expected_digest_gets_its_real_sha256(src, dst):
    r = copy_file(src, "hitsave-dumps", "dumps/a/data/disc.iso", dst, "copy", "k", None, part_size=5 * 2**20)
    assert r.ok and dst.objects["k"].metadata["sha256"] == sha256_of(BIG)
