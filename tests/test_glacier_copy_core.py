"""Copying one package from Wasabi to S3 Glacier Deep Archive (scripts/glacier_copy_core.py)."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from glacier_copy_core import TargetObject as T  # noqa: E402
from glacier_copy_core import copy_file, copy_package, expected_digests, plan_package  # noqa: E402
from glacier_fakes import FakeS3, client_error, crc64nvme_b64, sha256_of  # noqa: E402
import glacier_copy_core as core  # noqa: E402

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


def test_file_in_the_manifest_but_missing_on_wasabi_is_damage_not_a_deletion():
    src, dst = FakeS3(), FakeS3()
    _bag(src, "dumps/a/", {"data/disc.iso": DATA, "bagit.txt": b"BagIt-Version: 1.0\n"})
    copy_package(src, "hitsave-dumps", "dumps/a", dst, "copy")
    src.delete_object(Bucket="b", Key="dumps/a/data/disc.iso")  # lost on Wasabi, still in the manifest
    r = copy_package(src, "hitsave-dumps", "dumps/a", dst, "copy")
    assert r.problems == ["data/disc.iso is in the bag manifest but missing on Wasabi"]
    assert r.delete_markers == 0
    assert "hitsave-dumps/dumps/a/data/disc.iso" in dst.objects


def test_first_copy_of_a_bag_with_a_missing_file_fails():
    src, dst = FakeS3(), FakeS3()
    _bag(src, "dumps/a/", {"data/disc.iso": DATA})
    src.delete_object(Bucket="b", Key="dumps/a/data/disc.iso")
    r = copy_package(src, "hitsave-dumps", "dumps/a", dst, "copy")
    assert r.problems == ["data/disc.iso is in the bag manifest but missing on Wasabi"]


class InFlight:
    """Counts how many get_object calls run at the same time."""

    def __init__(self) -> None:
        self.now = self.most = 0
        self.lock = threading.Lock()

    def __call__(self, key) -> None:
        with self.lock:
            self.now += 1
            self.most = max(self.most, self.now)
        time.sleep(0.05)
        with self.lock:
            self.now -= 1


def test_large_file_parts_are_read_with_parallel_range_requests(src, dst):
    seen = InFlight()
    src.on_get = seen
    mib5 = 5 * 2**20
    r = copy_file(src, "hitsave-dumps", "dumps/a/data/disc.iso", dst, "copy", "k", sha256_of(BIG),
                  part_size=mib5, size=len(BIG), workers=3)
    assert r.ok and dst.objects["k"].data == BIG
    assert {rng for _, rng in src.ranges} == {
        f"bytes=0-{mib5 - 1}", f"bytes={mib5}-{2 * mib5 - 1}", f"bytes={2 * mib5}-{len(BIG) - 1}"}
    assert seen.most >= 2
    assert dst.completed[-1]["Parts"] == [1, 2, 3]
    assert dst.completed[-1]["ChecksumCRC64NVME"] == crc64nvme_b64(BIG)


def test_large_file_size_is_read_from_the_source_when_not_given(src, dst):
    r = copy_file(src, "hitsave-dumps", "dumps/a/data/disc.iso", dst, "copy", "k", sha256_of(BIG),
                  part_size=5 * 2**20)
    assert r.ok and r.bytes == len(BIG)


def test_source_shorter_than_its_listed_size_aborts_the_upload(src, dst):
    with pytest.raises(RuntimeError, match="changed during the copy"):
        copy_file(src, "hitsave-dumps", "dumps/a/data/disc.iso", dst, "copy", "k", sha256_of(BIG),
                  part_size=5 * 2**20, size=len(BIG) + 2**20)
    assert dst.aborted == ["k"] and "k" not in dst.objects


def test_part_upload_error_aborts_and_raises(src, dst):
    real = dst.upload_part

    def failing(**kw):
        if kw["PartNumber"] == 2:
            raise client_error("InternalError", "UploadPart", 500)
        return real(**kw)
    dst.upload_part = failing
    with pytest.raises(Exception, match="InternalError"):
        copy_file(src, "hitsave-dumps", "dumps/a/data/disc.iso", dst, "copy", "k", sha256_of(BIG),
                  part_size=5 * 2**20, workers=3)
    assert dst.aborted == ["k"] and "k" not in dst.objects


def test_copy_package_copies_small_files_in_parallel():
    src, dst = FakeS3(), FakeS3()
    _bag(src, "dumps/a/", {f"data/{n}.bin": DATA + bytes([n]) for n in range(6)})
    seen = InFlight()
    src.on_get = seen
    r = copy_package(src, "hitsave-dumps", "dumps/a", dst, "copy", workers=4)
    assert r.ok and r.files_uploaded == 8
    assert seen.most >= 2


def test_copy_package_with_one_worker_still_copies_everything():
    src, dst = FakeS3(), FakeS3()
    _bag(src, "dumps/a/", {"data/disc.iso": BIG, "bagit.txt": b"BagIt-Version: 1.0\n"})
    r = copy_package(src, "hitsave-dumps", "dumps/a", dst, "copy", part_size=5 * 2**20, workers=1)
    assert r.ok and dst.objects["hitsave-dumps/dumps/a/data/disc.iso"].data == BIG


def test_wasabi_source_client_skips_botocore_response_checksums():
    # The copy checks every file's SHA-256 itself; botocore's response checksum path crashed on an
    # error response (AttributeError: 'StreamingChecksumBody' object has no attribute 'strip').
    client = core.make_wasabi_source_client(
        {"endpoint": "https://s3.us-east-1.wasabisys.com", "region": "us-east-1"},
        {"access_key_id": "AKIAEXAMPLE", "secret_access_key": "secret"},
    )
    assert client.meta.config.response_checksum_validation == "when_required"
