"""Getting files back from the Glacier copy (scripts/glacier-restore.py)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from glacier_copy_core import copy_package  # noqa: E402
from glacier_fakes import FakeS3, client_error, sha256_of  # noqa: E402
from test_glacier_copy_job import PAYLOAD, make_bag  # noqa: E402

_spec = importlib.util.spec_from_file_location("glacier_restore", ROOT / "scripts" / "glacier-restore.py")
restore = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(restore)

PREFIX = "hitsave-dumps/dumps/pkg/"


@pytest.fixture
def dst():
    src, dst = FakeS3(), FakeS3()
    make_bag(src, "dumps/pkg/")
    copy_package(src, "hitsave-dumps", "dumps/pkg", dst, "copy")
    return dst


def run(dst, *argv):
    return restore.run(list(argv), dst_s3=dst, dst_bucket="copy", prefix_for=lambda key: PREFIX)


def ready_all(dst):
    for v in dst.objects.values():
        v.restored = True


def test_request_restores_every_file_once_with_the_chosen_tier(dst):
    out = run(dst, "request", "--game-key", "pkg", "--tier", "Standard")
    assert out["requested"] == 4
    assert sorted(r["Key"] for r in dst.restores) == sorted(dst.objects)
    assert all(r["GlacierJobParameters"]["Tier"] == "Standard" and r["Days"] == 7 for r in dst.restores)


def test_request_one_file_defaults_to_bulk(dst):
    run(dst, "request", "--game-key", "pkg", "--file", "data/disc.iso")
    assert dst.restores == [{"Key": PREFIX + "data/disc.iso", "Days": 7,
                             "GlacierJobParameters": {"Tier": "Bulk"}}]


def test_request_already_in_progress_is_not_an_error(dst):
    def busy(**kw):
        raise client_error("RestoreAlreadyInProgress", "RestoreObject", 409)
    dst.restore_object = busy
    assert run(dst, "request", "--game-key", "pkg")["already_requested"] == 4


def test_status_lists_ready_and_waiting_files(dst):
    run(dst, "request", "--game-key", "pkg")
    dst.objects[PREFIX + "data/disc.iso"].restored = True
    out = run(dst, "status", "--game-key", "pkg")
    assert out["ready"] == ["data/disc.iso"] and len(out["waiting"]) == 3


def test_fetch_downloads_ready_files_and_checks_them(dst, tmp_path):
    ready_all(dst)
    out = run(dst, "fetch", "--game-key", "pkg", "--to", str(tmp_path))
    assert out["fetched"] == 4 and out["problems"] == []
    assert (tmp_path / "data" / "disc.iso").read_bytes() == PAYLOAD


def test_fetch_refuses_to_overwrite(dst, tmp_path):
    ready_all(dst)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "disc.iso").write_bytes(b"keep me")
    out = run(dst, "fetch", "--game-key", "pkg", "--to", str(tmp_path))
    assert (tmp_path / "data" / "disc.iso").read_bytes() == b"keep me"
    assert "already exists: data/disc.iso" in out["problems"]


def test_fetch_reports_a_file_that_differs_from_the_manifest(dst, tmp_path):
    ready_all(dst)
    obj = dst.objects[PREFIX + "data/disc.iso"]
    obj.data = b"bit rot"
    out = run(dst, "fetch", "--game-key", "pkg", "--to", str(tmp_path))
    assert "SHA-256 differs from the bag manifest: data/disc.iso" in out["problems"]
    assert obj.metadata["sha256"] == sha256_of(PAYLOAD)


def test_fetch_skips_files_not_restored_yet(dst, tmp_path):
    out = run(dst, "fetch", "--game-key", "pkg", "--to", str(tmp_path))
    assert out["fetched"] == 0 and out["not_ready"] == 4


def test_fetch_never_writes_outside_the_target(dst, tmp_path):
    dst.put(PREFIX + "../escape.txt", b"x", metadata={"sha256": sha256_of(b"x")})
    ready_all(dst)
    out = run(dst, "fetch", "--game-key", "pkg", "--to", str(tmp_path / "out"))
    assert not (tmp_path / "escape.txt").exists()
    assert "unsafe path: ../escape.txt" in out["problems"]
