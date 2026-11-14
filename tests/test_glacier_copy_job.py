"""The nightly copy job (scripts/glacier-copy.py)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from glacier_fakes import FakeS3, sha256_of  # noqa: E402

_spec = importlib.util.spec_from_file_location("glacier_copy", ROOT / "scripts" / "glacier-copy.py")
job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(job)

PAYLOAD = b"disc image bytes"


def make_bag(s3: FakeS3, root: str, payload: bytes = PAYLOAD, note: bytes = b"v1") -> bytes:
    manifest = f"{sha256_of(payload)}  data/disc.iso\n".encode()
    s3.put(root + "data/disc.iso", payload)
    s3.put(root + "manifest-sha256.txt", manifest)
    s3.put(root + "bag-info.txt", note)
    tag = (f"{sha256_of(manifest)} manifest-sha256.txt\n{sha256_of(note)} bag-info.txt\n").encode()
    s3.put(root + "tagmanifest-sha256.txt", tag)
    return tag


class FakeLedger:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.package_copy: list[dict] = []
        self.fixity_run: list[dict] = []

    def candidates(self, kind=None, game_keys=None):
        out = []
        for r in self.rows:
            if game_keys and r["game_key"] not in game_keys:
                continue
            pc = next((c for c in self.package_copy if c["game_ingest_id"] == r["id"]), None)
            out.append({**r, "copied_fingerprint": pc and pc["tagmanifest_sha256"],
                        "copied_at": pc and pc["copied_at"]})
        return sorted(out, key=lambda r: (r["copied_at"] is not None, r["copied_at"] or 0))

    def record_copy(self, row):
        self.package_copy = [c for c in self.package_copy if c["game_ingest_id"] != row["game_ingest_id"]]
        self.package_copy.append({**row, "copied_at": len(self.package_copy) + 1})

    def record_fixity(self, game_ingest_id, outcome, files_checked, detail):
        self.fixity_run.append({"game_ingest_id": game_ingest_id, "outcome": outcome,
                                "files_checked": files_checked, "detail": detail})


def row(i, key, prefix, dc=None):
    return {"id": i, "game_key": key, "ingest_kind": "dump", "package_storage_prefix": prefix,
            "disc_contents_prefix": dc, "total_bytes": 100}


@pytest.fixture
def env():
    src, dst = FakeS3(), FakeS3()
    make_bag(src, "dumps/pkg/")
    ledger = FakeLedger([row(1, "pkg", "dumps/pkg")])

    def run(argv):
        return job.run(argv, ledger=ledger, src_s3=src, dst_s3=dst, dst_bucket="copy",
                       bucket_for=lambda kind, prefix: "hitsave-dumps")

    return src, dst, ledger, run


def test_due_means_no_copy_or_a_different_tagmanifest():
    rows = [
        {"game_key": "never-copied", "copied_fingerprint": None, "fingerprint": "a"},
        {"game_key": "up-to-date", "copied_fingerprint": "b", "fingerprint": "b"},
        {"game_key": "changed-since-copy", "copied_fingerprint": "c", "fingerprint": "d"},
    ]
    assert job.due_game_keys(rows) == ["never-copied", "changed-since-copy"]


def test_report_only_by_default_writes_nothing(env):
    src, dst, ledger, run = env
    out = run(["--max-packages", "5"])
    assert dst.puts == [] and ledger.package_copy == [] and ledger.fixity_run == []
    assert out["packages_due"] == 1 and out["executed"] is False


def test_tagmanifest_changed_during_copy_records_nothing(env):
    src, dst, ledger, run = env
    seen = []

    def change(key):
        if key.endswith("data/disc.iso") and not seen:
            seen.append(key)
            make_bag(src, "dumps/pkg/", note=b"v2")
    src.on_get = change
    out = run(["--execute", "--game-key", "pkg"])
    assert out["skipped_changed"] == 1 and ledger.package_copy == [] and ledger.fixity_run == []


def test_success_records_package_copy_and_a_glacier_fixity_row(env):
    src, dst, ledger, run = env
    out = run(["--execute", "--game-key", "pkg"])
    tag = src.objects["dumps/pkg/tagmanifest-sha256.txt"].data
    assert out["packages_copied"] == 1 and out["failed"] == 0
    pc = ledger.package_copy[0]
    assert pc["tagmanifest_sha256"] == sha256_of(tag)
    assert pc["location"] == "aws-deep-archive" and pc["bucket"] == "copy"
    assert pc["prefix"] == "hitsave-dumps/dumps/pkg/" and pc["object_count"] == 4
    assert ledger.fixity_run[0]["detail"].startswith("glacier-copy:")
    assert ledger.fixity_run[0]["outcome"] == "pass"


def test_second_run_finds_nothing_due_until_the_bag_changes(env):
    src, dst, ledger, run = env
    run(["--execute"])
    assert run(["--execute"])["packages_due"] == 0
    make_bag(src, "dumps/pkg/", note=b"v2")
    out = run(["--execute"])
    assert out["packages_due"] == 1 and out["packages_copied"] == 1


def test_disc_contents_bag_is_copied_and_part_of_the_fingerprint(env):
    src, dst, ledger, run = env
    ledger.rows[0]["disc_contents_prefix"] = "disc-contents/pkg"
    make_bag(src, "disc-contents/pkg/", payload=b"contents")
    run(["--execute"])
    assert "hitsave-dumps/disc-contents/pkg/data/disc.iso" in dst.objects
    make_bag(src, "disc-contents/pkg/", payload=b"contents", note=b"v2")
    assert run(["--execute"])["packages_copied"] == 1


def test_damaged_file_records_a_failure_and_no_copy(env):
    src, dst, ledger, run = env
    src.put("dumps/pkg/data/disc.iso", b"damaged!")
    out = run(["--execute"])
    assert out["failed"] == 1 and ledger.package_copy == []
    assert ledger.fixity_run[0]["outcome"] == "fail"
    assert "data/disc.iso" in ledger.fixity_run[0]["detail"]


def test_access_denied_stops_the_run_without_recording_failures(env):
    src, dst, ledger, run = env
    dst.deny_all()
    with pytest.raises(job.FatalError):
        run(["--execute"])
    assert ledger.fixity_run == [] and ledger.package_copy == []


def test_max_gb_stops_before_the_budget(env):
    src, dst, ledger, run = env
    make_bag(src, "dumps/two/")
    ledger.rows.append({**row(2, "two", "dumps/two"), "total_bytes": 10**10})
    out = run(["--execute", "--max-gb", "1"])
    assert out["packages_copied"] == 1


def test_settings_point_at_the_decided_bucket_and_region():
    import yaml
    aws = yaml.safe_load((ROOT / "config" / "preservation" / "aws-copy.yaml").read_text())
    assert aws["region"] == "us-east-2" and aws["bucket"] == "hitsave-backup-glacier"
    assert aws["credentials_file"] == "secrets/aws-glacier-copy.yaml"
