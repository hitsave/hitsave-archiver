"""prune-local-working-copies.py: only delete local copies proven identical on Wasabi."""
from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path

import pytest

import s3_etag

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("prune", ROOT / "scripts" / "prune-local-working-copies.py")
prune = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prune)

PREFIX = "dumps/f/pkg-1"
BUCKET = "hitsave-dumps"
SLUG = "redump-pkg"


def _etag(data: bytes) -> str:
    part = s3_etag.DEFAULT_PART_SIZE
    if len(data) < part:
        return hashlib.md5(data).hexdigest()
    parts = [hashlib.md5(data[i : i + part]).digest() for i in range(0, len(data), part)]
    return f"{hashlib.md5(b''.join(parts)).hexdigest()}-{len(parts)}"


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)
        self.etag_override: dict[str, str] = {}

    def get_paginator(self, name):
        s3 = self

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k, "Size": len(v)} for k, v in s3.objects.items() if k.startswith(Prefix)]}

        return P()

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        data = self.objects[Key]
        return {"ContentLength": len(data), "ETag": f'"{self.etag_override.get(Key, _etag(data))}"'}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def world(tmp_path: Path):
    payload = {f"data/{SLUG}/disc.iso": b"ISO" * 1000, f"data/{SLUG}/!submissionInfo.txt": b"info"}
    bag_root = tmp_path / "bag"
    bag = bag_root / "pkg-1"
    for rel, data in payload.items():
        (bag / rel).parent.mkdir(parents=True, exist_ok=True)
        (bag / rel).write_bytes(data)
    ready_root = tmp_path / "ready"
    ready = ready_root / SLUG
    ready.mkdir(parents=True)
    for rel, data in payload.items():
        (ready / Path(rel).name).write_bytes(data)
    (ready / "hitsave-submission.yaml").write_text("redump: {}\n")
    (ready / "game.ingest.yaml").write_text("ingest_kind: dump\n")

    manifest = "".join(f"{_sha(d)}  {rel}\n" for rel, d in payload.items()).encode()
    baginfo = b"Bag-Software-Agent: test\n"
    tagmanifest = f"{_sha(manifest)}  manifest-sha256.txt\n{_sha(baginfo)}  bag-info.txt\n".encode()
    objects = {f"{PREFIX}/{rel}": d for rel, d in payload.items()}
    objects.update({
        f"{PREFIX}/manifest-sha256.txt": manifest,
        f"{PREFIX}/bag-info.txt": baginfo,
        f"{PREFIX}/tagmanifest-sha256.txt": tagmanifest,
    })
    inventory = {"files": [{"path": rel, "sha256": _sha(d), "size_bytes": len(d)} for rel, d in payload.items()]}
    c = prune.Candidate(1, "pkg-1", "dump", PREFIX, BUCKET, inventory, bag, ready)
    return {"s3": FakeS3(objects), "c": c, "bag_root": bag_root, "ready_root": ready_root}


def test_verified_package_passes_and_prune_keeps_handoff(world) -> None:
    problems, hashed = prune.verify(world["s3"], world["c"])
    assert problems == []
    assert hashed > 0
    removed = prune.prune(world["c"], now="2026-11-01T00:00:00+00:00")
    assert not world["c"].bag_dir.exists()
    ready = world["c"].ready_dir
    assert sorted(p.name for p in ready.iterdir()) == ["PRUNED.txt", "game.ingest.yaml", "hitsave-submission.yaml"]
    assert "s3://hitsave-dumps/dumps/f/pkg-1/" in (ready / "PRUNED.txt").read_text()
    assert len(removed) == 3


def test_verifies_from_ready_when_bag_already_gone(world) -> None:
    import shutil

    shutil.rmtree(world["c"].bag_dir)
    world["c"].bag_dir = None
    assert prune.verify(world["s3"], world["c"])[0] == []


def test_etag_mismatch_blocks(world) -> None:
    world["s3"].etag_override[f"{PREFIX}/data/{SLUG}/disc.iso"] = "0" * 32
    problems, _ = prune.verify(world["s3"], world["c"])
    assert any("ETag does not match" in p for p in problems)


def test_local_bitrot_blocks(world) -> None:
    (world["c"].bag_dir / f"data/{SLUG}/disc.iso").write_bytes(b"ISX" * 1000)
    problems, _ = prune.verify(world["s3"], world["c"])
    assert any("no longer matches ingest SHA-256" in p for p in problems)


def test_missing_wasabi_object_blocks(world) -> None:
    del world["s3"].objects[f"{PREFIX}/data/{SLUG}/disc.iso"]
    problems, _ = prune.verify(world["s3"], world["c"])
    assert any("missing or wrong size" in p for p in problems)


def test_stray_wasabi_payload_blocks(world) -> None:
    world["s3"].objects[f"{PREFIX}/data/{SLUG}/old.bin"] = b"x"
    problems, _ = prune.verify(world["s3"], world["c"])
    assert any("stray Wasabi payload" in p for p in problems)


def test_wasabi_tag_file_corruption_blocks(world) -> None:
    world["s3"].objects[f"{PREFIX}/bag-info.txt"] = b"changed\n"
    problems, _ = prune.verify(world["s3"], world["c"])
    assert any("tag file SHA-256 mismatch: bag-info.txt" in p for p in problems)


def test_wasabi_manifest_diverging_from_inventory_blocks(world) -> None:
    world["c"].inventory["files"][0]["sha256"] = "0" * 64
    problems, _ = prune.verify(world["s3"], world["c"])
    assert any("differs from ingest inventory" in p for p in problems)


def test_unpreserved_local_file_blocks(world) -> None:
    (world["c"].ready_dir / "extra-scan.png").write_bytes(b"png")
    problems, _ = prune.verify(world["s3"], world["c"])
    assert any("ready file not in ingest inventory" in p for p in problems)
    (world["c"].ready_dir / "extra-scan.png").unlink()
    (world["c"].bag_dir / f"data/{SLUG}/.hidden").write_bytes(b"x")
    problems, _ = prune.verify(world["s3"], world["c"])
    assert any("local bag file not in ingest inventory" in p for p in problems)


def test_stale_local_tag_files_do_not_matter(world) -> None:
    """Local tag files may lag Wasabi (renamed packages); only Wasabi's tag files are checked."""
    (world["c"].bag_dir / "bag-info.txt").write_text("stale\n")
    assert prune.verify(world["s3"], world["c"])[0] == []


def test_empty_inventory_blocks(world) -> None:
    world["c"].inventory = {"files": []}
    assert prune.verify(world["s3"], world["c"])[0] == ["ledger inventory is empty"]


def test_contained_dir(tmp_path: Path) -> None:
    root = tmp_path / "bag"
    (root / "a").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (root / "link").symlink_to(tmp_path / "outside")
    assert prune.contained_dir(root / "a", root) == (root / "a").resolve()
    assert prune.contained_dir(root, root) is None
    assert prune.contained_dir(root / "link", root) is None
    assert prune.contained_dir(tmp_path / "outside", root) is None
    assert prune.contained_dir(f"{root}/a/../a", root) is None
    assert prune.contained_dir("relative/a", root) is None
    assert prune.contained_dir(None, root) is None


def test_ready_dir_for(tmp_path: Path) -> None:
    (tmp_path / SLUG).mkdir()
    assert prune.ready_dir_for(f"/data/gdrive-intake/ready/{SLUG}", tmp_path) == (tmp_path / SLUG).resolve()
    assert prune.ready_dir_for("/data/press-material/Some Game", tmp_path) is None
    assert prune.ready_dir_for(f"/data/gdrive-intake/ready/{SLUG}/sub", tmp_path) is None
    assert prune.ready_dir_for("/data/gdrive-intake/ready/missing", tmp_path) is None
    assert prune.ready_dir_for(None, tmp_path) is None
