"""Monthly check of the Glacier copy without restores (scripts/glacier-copy-check.py)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from glacier_copy_core import copy_package, fingerprint  # noqa: E402
from glacier_fakes import FakeS3  # noqa: E402
from test_glacier_copy_job import make_bag  # noqa: E402

_spec = importlib.util.spec_from_file_location("glacier_copy_check", ROOT / "scripts" / "glacier-copy-check.py")
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

DST = "hitsave-dumps/dumps/pkg/"


class FakeLedger:
    def __init__(self, fp):
        self.rows = [{"id": 1, "game_key": "pkg", "ingest_kind": "dump", "package_storage_prefix": "dumps/pkg",
                      "disc_contents_prefix": None, "tagmanifest_sha256": fp}]
        self.fixity_run: list[dict] = []
        self.verified: list[int] = []

    def copies(self, game_keys=None):
        return [r for r in self.rows if not game_keys or r["game_key"] in game_keys]

    def record_fixity(self, game_ingest_id, outcome, files_checked, detail):
        self.fixity_run.append({"outcome": outcome, "files_checked": files_checked, "detail": detail})

    def mark_verified(self, game_ingest_id):
        self.verified.append(game_ingest_id)


@pytest.fixture
def env():
    src, dst = FakeS3(), FakeS3()
    make_bag(src, "dumps/pkg/")
    copy_package(src, "hitsave-dumps", "dumps/pkg", dst, "copy")
    ledger = FakeLedger(fingerprint(src, "hitsave-dumps", ["dumps/pkg"]))

    def run(argv=()):
        return check.run(list(argv), ledger=ledger, src_s3=src, dst_s3=dst, dst_bucket="copy",
                         bucket_for=lambda kind, prefix: "hitsave-dumps")

    return src, dst, ledger, run


def test_matching_copy_passes_and_is_marked_verified(env):
    src, dst, ledger, run = env
    out = run()
    assert out["passed"] == 1 and out["failed"] == 0
    assert ledger.fixity_run[0]["outcome"] == "pass"
    assert ledger.fixity_run[0]["detail"].startswith("glacier:")
    assert ledger.verified == [1]


def test_missing_file_fails_and_names_it(env):
    src, dst, ledger, run = env
    dst.delete_object(Bucket="copy", Key=DST + "data/disc.iso")
    out = run()
    assert out["failed"] == 1 and ledger.verified == []
    assert "missing in the copy: data/disc.iso" in ledger.fixity_run[0]["detail"]


def test_wrong_size_or_digest_fails(env):
    src, dst, ledger, run = env
    dst.put(DST + "data/disc.iso", b"other bytes, other size", metadata={"sha256": "0" * 64})
    dst.put(DST + "bag-info.txt", b"v9", metadata={"sha256": "0" * 64})
    run()
    detail = ledger.fixity_run[0]["detail"]
    assert "size differs: data/disc.iso" in detail
    assert "SHA-256 differs: bag-info.txt" in detail


def test_file_gone_from_wasabi_but_current_in_the_copy_is_reported(env):
    src, dst, ledger, run = env
    dst.put(DST + "data/old.iso", b"old", metadata={"sha256": "1" * 64})
    run()
    assert "not marked deleted: data/old.iso" in ledger.fixity_run[0]["detail"]


def test_out_of_date_copy_is_skipped_not_failed(env):
    src, dst, ledger, run = env
    make_bag(src, "dumps/pkg/", note=b"v2")
    out = run()
    assert out["skipped_out_of_date"] == 1 and ledger.fixity_run == []


def test_no_record_writes_nothing(env):
    src, dst, ledger, run = env
    run(["--no-record"])
    assert ledger.fixity_run == [] and ledger.verified == []


def test_access_denied_is_fatal(env):
    src, dst, ledger, run = env
    dst.deny_all()
    with pytest.raises(check.FatalError):
        run()
    assert ledger.fixity_run == []
