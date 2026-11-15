"""The catalog correction and the ledger metadata sync hold the package lock while they rewrite a bag."""
from __future__ import annotations

import importlib.util
import io
import json
from contextlib import contextmanager
from pathlib import Path

import sync_dump_bag_metadata_from_ledger as sync

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "press-material/T/tekken-7-01"


class Lock:
    def __init__(self):
        self.held = False
        self.keys: list[str] = []

    @contextmanager
    def __call__(self, game_key):
        self.keys.append(game_key)
        self.held = True
        try:
            yield
        finally:
            self.held = False


class FakeS3:
    def __init__(self, lock: Lock):
        self.lock = lock
        self.reads_without_lock: list[str] = []
        self.objects = {
            f"{PREFIX}/metadata/submission.json": json.dumps({"omeka_item_title": "T"}).encode(),
            f"{PREFIX}/bag-info.txt": b"Bag-Software: x\nExternal-Description: old\n",
            f"{PREFIX}/tagmanifest-sha256.txt": b"aaa  bag-info.txt\nbbb  metadata/submission.json\n",
        }

    def get_object(self, Bucket, Key):  # noqa: N803
        if not self.lock.held:
            self.reads_without_lock.append(Key)
        if Key not in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


def _uploads(monkeypatch, module, lock: Lock) -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []
    monkeypatch.setattr(module, "upload_file_to_s3",
                        lambda client, *, bucket, key, path: out.append((key, lock.held)))
    return out


def _patch_press(monkeypatch, fake):
    spec = importlib.util.spec_from_file_location("patch_press", ROOT / "scripts" / "patch-press-catalog-omeka-and-bag.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "load_wasabi_settings", lambda p: ({"credentials_file": "c"}, None))
    monkeypatch.setattr(mod, "load_yaml", lambda p: {})
    monkeypatch.setattr(mod, "resolve_config_path", lambda *a: Path("c"))
    monkeypatch.setattr(mod, "make_s3_client", lambda *a: fake)
    monkeypatch.setattr(mod, "wasabi_bucket_for_storage_prefix", lambda *a, **k: "b")
    return mod


def _correct(mod, lock, execute=True):
    mod.patch_wasabi_bag(ledger={"package_storage_prefix": PREFIX, "game_key": "tekken-7-01"}, title="Tekken 7",
                         reference_uri="https://www.mobygames.com/game/1", former_title="Tekken",
                         correction_summary="title fix", execute=execute, lock=lock)


def test_catalog_correction_reads_and_writes_the_bag_under_the_lock(monkeypatch) -> None:
    lock = Lock()
    fake = FakeS3(lock)
    mod = _patch_press(monkeypatch, fake)
    uploads = _uploads(monkeypatch, mod, lock)
    _correct(mod, lock)
    assert lock.keys == ["tekken-7-01"]
    assert fake.reads_without_lock == []
    assert uploads and all(held for _, held in uploads)


def test_catalog_correction_dry_run_takes_no_lock(monkeypatch) -> None:
    lock = Lock()
    mod = _patch_press(monkeypatch, FakeS3(lock))
    _uploads(monkeypatch, mod, lock)
    _correct(mod, lock, execute=False)
    assert lock.keys == []


def test_ledger_metadata_sync_reads_and_writes_the_bag_under_the_lock(monkeypatch) -> None:
    lock = Lock()
    fake = FakeS3(lock)
    uploads = _uploads(monkeypatch, sync, lock)
    sync.sync_bag_from_ledger(fake, bucket="b", storage_prefix=PREFIX,
                              ledger_row={"game_key": "tekken-7-01", "title_slug": "t", "sort_letter": "T"},
                              execute=True, lock=lock)
    assert lock.keys == ["tekken-7-01"]
    assert fake.reads_without_lock == []
    assert uploads and all(held for _, held in uploads)
