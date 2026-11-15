"""Checking Wasabi against the Glacier copy and writing verified files back (glacier_restore_core)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from glacier_copy_core import copy_package  # noqa: E402
from glacier_fakes import FakeS3, sha256_of  # noqa: E402
from glacier_restore_core import (  # noqa: E402
    FileCheck,
    check_bag,
    restore_progress,
    summary_message,
    verify_restored,
    write_back,
)
from test_glacier_copy_job import PAYLOAD, make_bag  # noqa: E402

BUCKET = "hitsave-dumps"
BAG = "dumps/pkg"
COPY = "copy"
JUNK = b"bad bytes"


def setup(note: bytes = b"v1"):
    wasabi, aws = FakeS3(), FakeS3()
    tag = make_bag(wasabi, BAG + "/", note=note)
    copy_package(wasabi, BUCKET, BAG, aws, COPY)
    return wasabi, aws, sha256_of(tag)


def check(wasabi, aws, recorded):
    return check_bag(wasabi, BUCKET, BAG, aws, COPY, recorded)


def restore_all(aws: FakeS3) -> None:
    for v in aws.objects.values():
        if v.restore_requests:
            v.restored = True


def fc(rel="data/disc.iso", verdict="ok", restorable=False, sha=None):
    return FileCheck(f"{BUCKET}/{BAG}/", rel, len(PAYLOAD), sha or sha256_of(PAYLOAD), verdict, restorable)


def test_check_all_ok_when_wasabi_matches_copy():
    wasabi, aws, recorded = setup()
    files = check(wasabi, aws, recorded)
    assert {f.verdict for f in files} == {"ok"}
    assert {f.rel for f in files} == {"data/disc.iso", "manifest-sha256.txt", "bag-info.txt",
                                      "tagmanifest-sha256.txt"}


def test_check_finds_corrupted_and_missing_files():
    wasabi, aws, recorded = setup()
    wasabi.put(BAG + "/data/disc.iso", JUNK)
    wasabi.delete_object(Bucket=BUCKET, Key=BAG + "/bag-info.txt")
    files = check(wasabi, aws, recorded)
    assert {(f.rel, f.verdict, f.restorable) for f in files if f.verdict != "ok"} == {
        ("data/disc.iso", "corrupted", True), ("bag-info.txt", "missing", True)}


def test_check_labels_newer_files_and_leaves_them_alone():
    wasabi, aws, recorded = setup()
    make_bag(wasabi, BAG + "/", note=b"v2")
    files = check(wasabi, aws, recorded)
    changed = {f.rel: f for f in files if f.verdict != "ok"}
    assert set(changed) == {"bag-info.txt", "tagmanifest-sha256.txt"}
    assert {f.verdict for f in changed.values()} == {"newer"}
    assert summary_message(files)[0] == "not_needed"


def test_check_corrupted_file_with_older_copy_is_not_restorable():
    wasabi, aws, recorded = setup()
    make_bag(wasabi, BAG + "/", note=b"v2")
    wasabi.put(BAG + "/bag-info.txt", JUNK)
    files = check(wasabi, aws, recorded)
    bad = [f for f in files if f.verdict == "corrupted"]
    assert [f.rel for f in bad] == ["bag-info.txt"] and bad[0].restorable is False
    assert bad[0].note == "The copy only has an older version of this file."
    assert summary_message(files)[0] == "cannot_restore"


def test_check_newer_bag_with_corrupted_unchanged_file_is_restorable():
    wasabi, aws, recorded = setup()
    make_bag(wasabi, BAG + "/", note=b"v2")
    wasabi.put(BAG + "/data/disc.iso", JUNK)
    files = check(wasabi, aws, recorded)
    assert [(f.rel, f.restorable) for f in files if f.verdict == "corrupted"] == [("data/disc.iso", True)]


def test_check_uses_copy_digests_when_payload_manifest_is_corrupted():
    wasabi, aws, recorded = setup()
    wasabi.put(BAG + "/manifest-sha256.txt", JUNK)
    wasabi.put(BAG + "/tagmanifest-sha256.txt", wasabi.objects[BAG + "/tagmanifest-sha256.txt"].data)
    files = check(wasabi, aws, "something-else")
    assert {(f.rel, f.verdict, f.restorable) for f in files if f.verdict != "ok"} == {
        ("manifest-sha256.txt", "corrupted", True)}


def test_summary_messages():
    ok, newer = fc(), fc(verdict="newer")
    corrupted, missing = fc(verdict="corrupted", restorable=True), fc(verdict="missing", restorable=True)
    stuck = fc(verdict="corrupted", restorable=False)
    assert summary_message([ok] * 40 + [corrupted, missing]) == (
        "restoring", "Restoring 1 corrupted and 1 missing file. 40 files on Wasabi are fine and won't be restored.")
    assert summary_message([ok, newer]) == (
        "not_needed", "No file on Wasabi is missing or corrupted. Nothing needs restoring.")
    assert summary_message([ok, stuck]) == (
        "cannot_restore", "1 file is missing or corrupted, but the copy only has an older version of it. "
        "See docs/restore-from-glacier.md.")
    assert summary_message([ok, corrupted, corrupted, stuck]) == (
        "restoring", "Restoring 2 corrupted files. 1 file on Wasabi is fine and won't be restored. "
        "1 more file can't be restored, because the copy only has an older version of it.")


def test_restore_progress_counts_ready_and_reads_expiry():
    wasabi, aws, recorded = setup()
    files = [f for f in check(wasabi, aws, recorded) if f.rel in ("data/disc.iso", "bag-info.txt")]
    for f in files:
        aws.restore_object(Bucket=COPY, Key=f.bag + f.rel, RestoreRequest={})
    aws.objects[f"{BUCKET}/{BAG}/data/disc.iso"].restored = True
    assert restore_progress(aws, COPY, files) == (1, 1, datetime(2026, 11, 27, tzinfo=timezone.utc))


def test_verify_restored_reports_a_mismatch():
    wasabi, aws, recorded = setup()
    good = fc(verdict="corrupted", restorable=True)
    bad = fc(verdict="corrupted", restorable=True, sha="0" * 64)
    aws.restore_object(Bucket=COPY, Key=good.bag + good.rel, RestoreRequest={})
    restore_all(aws)
    assert verify_restored(aws, COPY, [good]) == []
    assert verify_restored(aws, COPY, [bad]) == ["data/disc.iso: the restored file doesn't match its SHA-256"]


def test_write_back_replaces_corrupted_file_with_verified_bytes():
    wasabi, aws, recorded = setup()
    wasabi.put(BAG + "/data/disc.iso", JUNK)
    f = fc(verdict="corrupted", restorable=True)
    aws.restore_object(Bucket=COPY, Key=f.bag + f.rel, RestoreRequest={})
    restore_all(aws)
    assert write_back(aws, COPY, wasabi, BUCKET, f) == ("yes", "")
    stored = wasabi.objects[BAG + "/data/disc.iso"]
    assert stored.data == PAYLOAD and stored.storage_class == "STANDARD"
    assert stored.metadata == {"sha256": sha256_of(PAYLOAD)}


def test_write_back_restores_a_missing_file():
    wasabi, aws, recorded = setup()
    wasabi.delete_object(Bucket=BUCKET, Key=BAG + "/data/disc.iso")
    f = fc(verdict="missing", restorable=True)
    aws.restore_object(Bucket=COPY, Key=f.bag + f.rel, RestoreRequest={})
    restore_all(aws)
    assert write_back(aws, COPY, wasabi, BUCKET, f) == ("yes", "")
    assert wasabi.objects[BAG + "/data/disc.iso"].data == PAYLOAD


def test_write_skips_file_that_now_matches():
    wasabi, aws, recorded = setup()
    puts = len(wasabi.puts)
    f = fc(verdict="corrupted", restorable=True)
    assert write_back(aws, COPY, wasabi, BUCKET, f) == ("skipped", "already correct on Wasabi, not written")
    assert len(wasabi.puts) == puts


def test_write_back_never_stores_wrong_bytes():
    wasabi, aws, recorded = setup()
    wasabi.put(BAG + "/data/disc.iso", JUNK)
    key = f"{BUCKET}/{BAG}/data/disc.iso"
    v = aws.objects[key]
    aws.put(key, b"X" * len(PAYLOAD), storage_class="DEEP_ARCHIVE", metadata=dict(v.metadata))
    aws.objects[key].restored = True
    status, note = write_back(aws, COPY, wasabi, BUCKET, fc(verdict="corrupted", restorable=True))
    assert status == "no" and "SHA-256" in note
    assert wasabi.objects[BAG + "/data/disc.iso"].data == JUNK


def test_file_check_round_trips_through_json():
    f = fc(verdict="missing", restorable=True)
    assert FileCheck.from_json(f.to_json()) == f


def test_copy_file_to_wasabi_in_parts_sends_no_storage_class_or_checksums():
    from glacier_copy_core import WASABI, copy_file

    src, dst = FakeS3(), FakeS3()
    data = bytes(range(256)) * 40
    src.put("k", data)
    result = copy_file(src, "b", "k", dst, "w", "k", sha256_of(data), part_size=1024, target=WASABI)
    assert result.ok and dst.objects["k"].data == data and dst.objects["k"].storage_class == "STANDARD"
    done = dst.completed[-1]
    assert done["ChecksumCRC64NVME"] is None and done["ChecksumType"] is None
    assert "ChecksumAlgorithm" not in done
