"""The restore worker moves ledger restore requests forward (scripts/glacier-restore-worker.py)."""
from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from glacier_copy_core import FatalError, copy_package  # noqa: E402
from glacier_fakes import FakeS3, sha256_of  # noqa: E402
from test_glacier_copy_job import PAYLOAD, make_bag  # noqa: E402

_spec = importlib.util.spec_from_file_location("restore_worker", ROOT / "scripts" / "glacier-restore-worker.py")
worker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worker)

BUCKET, BAG, COPY = "hitsave-dumps", "dumps/pkg", "copy"
JUNK = b"J" * len(PAYLOAD)
NOV15 = datetime(2026, 11, 15, tzinfo=timezone.utc)
NOV28 = datetime(2026, 11, 28, tzinfo=timezone.utc)


class FakeLedger:
    def __init__(self, requests):
        self.requests = requests
        self.closed_reports: list[tuple] = []

    def open_requests(self):
        return [dict(r) for r in self.requests if r["state"] in worker.OPEN]

    def save(self, request_id, expect_state, **fields):
        r = next(r for r in self.requests if r["id"] == request_id)
        if r["state"] != expect_state:
            return False
        r.update(fields)
        return True

    def close_report(self, report_id, by, note):
        self.closed_reports.append((report_id, by, note))


class Env:
    def __init__(self, state="checking"):
        self.wasabi, self.aws = FakeS3(), FakeS3()
        tag = make_bag(self.wasabi, BAG + "/")
        copy_package(self.wasabi, BUCKET, BAG, self.aws, COPY)
        self.req = {"id": 1, "state": state, "tier": "Bulk", "files": [], "message": None,
                    "game_key": "pkg", "ingest_kind": "dump", "package_storage_prefix": BAG,
                    "disc_contents_prefix": None, "tagmanifest_sha256": sha256_of(tag),
                    "problem_report_id": 7, "restore_expires_at": None}
        self.ledger = FakeLedger([self.req])
        self.fixity_ok = True
        self.fixity_calls: list[tuple[str, bool]] = []
        self.now = NOV15
        self.locked: list[str] = []
        self.lock_held = False
        self.unlocked_wasabi_writes: list[str] = []
        store = self.wasabi._store

        def store_checked(key, v):
            if not self.lock_held:
                self.unlocked_wasabi_writes.append(key)
            store(key, v)

        self.wasabi._store = store_checked

    @contextmanager
    def lock(self, game_key):
        self.locked.append(game_key)
        self.lock_held = True
        try:
            yield
        finally:
            self.lock_held = False

    def run(self):
        def fixity(key):
            self.fixity_calls.append((key, self.lock_held))
            return self.fixity_ok
        return worker.run([], ledger=self.ledger, aws_s3=self.aws, aws_bucket=COPY, wasabi_s3=self.wasabi,
                          bucket_for=lambda kind, prefix: BUCKET, fixity_check=fixity, now=lambda: self.now,
                          lock=self.lock)

    def restore_all(self):
        for v in self.aws.objects.values():
            if v.restore_requests:
                v.restored = True

    def copy_key(self, rel):
        return f"{BUCKET}/{BAG}/{rel}"

    def to_awaiting_approval(self):
        self.wasabi.put(BAG + "/data/disc.iso", JUNK)
        self.run()
        self.restore_all()
        self.run()
        assert self.req["state"] == "awaiting_approval"


def test_checking_all_ok_ends_not_needed_without_restore_requests():
    env = Env()
    env.run()
    assert env.req["state"] == "not_needed" and env.aws.restores == []
    assert env.req["message"] == "No file on Wasabi is missing or corrupted. Nothing needs restoring."


def test_checking_requests_only_corrupted_files():
    env = Env()
    env.wasabi.put(BAG + "/data/disc.iso", JUNK)
    env.run()
    assert env.req["state"] == "restoring"
    assert [r["Key"] for r in env.aws.restores] == [env.copy_key("data/disc.iso")]
    assert env.aws.restores[0]["GlacierJobParameters"] == {"Tier": "Bulk"}
    assert env.req["message"].startswith("Restoring 1 corrupted file.")
    assert {f["rel"]: f["verdict"] for f in env.req["files"]}["data/disc.iso"] == "corrupted"


def test_checking_cannot_restore_requests_nothing():
    env = Env()
    make_bag(env.wasabi, BAG + "/", note=b"v2")
    env.wasabi.put(BAG + "/bag-info.txt", b"junk")
    env.run()
    assert env.req["state"] == "cannot_restore" and env.aws.restores == []


def test_restoring_waits_then_moves_to_awaiting_approval():
    env = Env()
    env.wasabi.put(BAG + "/data/disc.iso", JUNK)
    env.run()
    env.run()
    assert env.req["state"] == "restoring"
    env.restore_all()
    env.run()
    assert env.req["state"] == "awaiting_approval"
    assert env.req["restore_expires_at"] == datetime(2026, 11, 27, tzinfo=timezone.utc)


def test_restored_file_with_wrong_bytes_fails():
    env = Env()
    env.wasabi.put(BAG + "/data/disc.iso", JUNK)
    env.run()
    key = env.copy_key("data/disc.iso")
    env.aws.put(key, JUNK, storage_class="DEEP_ARCHIVE", metadata=dict(env.aws.objects[key].metadata))
    env.aws.objects[key].restored = True
    env.run()
    assert env.req["state"] == "failed"
    assert env.req["message"] == "data/disc.iso: the restored file doesn't match its SHA-256"


def test_awaiting_approval_never_writes():
    env = Env()
    env.to_awaiting_approval()
    env.run()
    assert env.req["state"] == "awaiting_approval"
    assert env.wasabi.objects[BAG + "/data/disc.iso"].data == JUNK


def test_awaiting_approval_expires_after_restore_window():
    env = Env()
    env.to_awaiting_approval()
    env.now = NOV28
    env.run()
    assert env.req["state"] == "expired"
    assert env.req["message"] == ("The restored files stopped being readable before anyone approved them. "
                                  "Start a new restore.")
    assert env.wasabi.objects[BAG + "/data/disc.iso"].data == JUNK


def test_approved_writes_runs_fixity_and_adds_recovery_event():
    env = Env()
    env.to_awaiting_approval()
    env.req["state"] = "approved"
    env.run()
    assert env.req["state"] == "done"
    assert env.wasabi.objects[BAG + "/data/disc.iso"].data == PAYLOAD
    assert env.fixity_calls == [("pkg", True)]
    events = {k: v.data for k, v in env.wasabi.objects.items() if "/metadata/premis/recovery-" in k}
    assert len(events) == 1 and b"recovery" in next(iter(events.values()))
    tag = env.wasabi.objects[BAG + "/tagmanifest-sha256.txt"].data.decode()
    assert next(iter(events)).removeprefix(BAG + "/") in tag
    assert env.ledger.closed_reports == [(7, "glacier-restore-worker", "Restored from the Glacier copy")]
    assert [f["written"] for f in env.req["files"] if f["rel"] == "data/disc.iso"] == ["yes"]


def test_approved_but_expired_writes_nothing():
    env = Env()
    env.to_awaiting_approval()
    env.req["state"] = "approved"
    env.now = NOV28
    env.run()
    assert env.req["state"] == "expired" and env.wasabi.objects[BAG + "/data/disc.iso"].data == JUNK


def test_writing_resumes_after_interruption():
    env = Env()
    env.wasabi.put(BAG + "/data/disc.iso", JUNK)
    env.wasabi.delete_object(Bucket=BUCKET, Key=BAG + "/bag-info.txt")
    env.run()
    env.restore_all()
    env.run()
    env.req["state"] = "writing"
    first = next(f for f in env.req["files"] if f["rel"] == "bag-info.txt")
    first["written"] = "yes"
    env.wasabi.put(BAG + "/bag-info.txt", b"v1")
    puts_before = env.wasabi.puts.count(BAG + "/bag-info.txt")
    env.run()
    assert env.wasabi.puts.count(BAG + "/bag-info.txt") == puts_before
    assert env.req["state"] == "done"


def test_failed_fixity_check_ends_failed():
    env = Env()
    env.to_awaiting_approval()
    env.req["state"] = "approved"
    env.fixity_ok = False
    env.run()
    assert env.req["state"] == "failed"
    assert env.req["message"] == ("The Wasabi fixity check failed after the restore. "
                                  "See the fixity row on the ledger page.")
    assert env.ledger.closed_reports == []


def test_cancelled_while_checking_is_left_alone():
    env = Env()
    env.wasabi.put(BAG + "/data/disc.iso", JUNK)
    real_save = env.ledger.save

    def cancel_first(request_id, expect_state, **fields):
        env.req["state"] = "cancelled"
        return real_save(request_id, expect_state, **fields)

    env.ledger.save = cancel_first
    env.run()
    assert env.req["state"] == "cancelled"


def test_access_denied_stops_without_changing_state():
    env = Env()
    env.aws.deny_all()
    with pytest.raises(FatalError):
        env.run()
    assert env.req["state"] == "checking"


def test_done_message():
    assert worker.done_message(1, 1) == "Restored 1 file."
    assert worker.done_message(1, 3) == "Restored 1 file. 2 were already correct on Wasabi, so they weren't written."


def test_write_saved_after_a_crash_still_counts_as_written():
    env = Env()
    env.to_awaiting_approval()
    env.req["state"] = "approved"
    real_save = env.ledger.save
    crashed = []

    def crash_after_write(request_id, expect_state, **fields):
        if not crashed and any(f.get("written") == "yes" for f in fields.get("files") or []):
            crashed.append(True)
            raise RuntimeError("container killed")
        return real_save(request_id, expect_state, **fields)

    env.ledger.save = crash_after_write
    env.run()
    assert env.wasabi.objects[BAG + "/data/disc.iso"].data == PAYLOAD
    env.run()
    assert env.req["state"] == "done" and env.req["message"] == "Restored 1 file."
    assert [f["written"] for f in env.req["files"] if f["rel"] == "data/disc.iso"] == ["yes"]
    assert any("/metadata/premis/recovery-" in k for k in env.wasabi.objects)


def test_fixity_passed_needs_one_checked_package_without_skips():
    ok = '{"packages_due": 1, "packages_checked": 1, "failed": 0, "skipped_changed": 0}'
    assert worker.fixity_passed(0, "PASS pkg: ...\n" + ok)
    assert not worker.fixity_passed(0, ok.replace('"skipped_changed": 0', '"skipped_changed": 1'))
    assert not worker.fixity_passed(0, ok.replace('"packages_checked": 1', '"packages_checked": 0'))
    assert not worker.fixity_passed(1, ok)
    assert not worker.fixity_passed(0, "no json")


def test_writing_holds_the_package_lock_for_every_wasabi_write_and_the_fixity_check():
    env = Env()
    env.to_awaiting_approval()
    assert env.locked == []  # checking and restoring only read Wasabi
    env.unlocked_wasabi_writes.clear()
    env.req["state"] = "approved"
    env.run()
    assert env.req["state"] == "done"
    assert env.locked == ["pkg"]
    assert env.unlocked_wasabi_writes == []
    assert env.fixity_calls == [("pkg", True)]


def test_package_busy_leaves_the_request_to_try_again():
    env = Env()
    env.to_awaiting_approval()
    env.req["state"] = "approved"

    @contextmanager
    def busy(game_key):
        raise worker.PackageBusy(f"another job is writing to package {game_key}; try again later")
        yield

    env.lock = busy
    env.run()
    assert env.req["state"] == "writing"
    assert "try again" in env.req["message"]
    assert env.wasabi.objects[BAG + "/data/disc.iso"].data == JUNK
