"""Scripts that change a bag on Wasabi leave a PREMIS event in it (uploaded before the tagmanifest)."""
from __future__ import annotations

import importlib.util
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import sync_dump_bag_metadata_from_ledger as sync
import apply_dvd_menu_bag_previews as dvd

ROOT = Path(__file__).resolve().parents[1]
P = "{http://www.loc.gov/premis/v3}"


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


def _bag(prefix: str) -> dict[str, bytes]:
    return {
        f"{prefix}/metadata/submission.json": json.dumps({"omeka_item_title": "T", "package_id": "old"}).encode(),
        f"{prefix}/bag-info.txt": b"Bag-Software: x\nExternal-Description: old\n",
        f"{prefix}/tagmanifest-sha256.txt": b"aaa  bag-info.txt\nbbb  metadata/submission.json\n",
    }


def _capture_uploads(monkeypatch, module) -> list[tuple[str, bytes]]:
    uploads: list[tuple[str, bytes]] = []
    monkeypatch.setattr(module, "upload_file_to_s3",
                        lambda client, *, bucket, key, path: uploads.append((key, Path(path).read_bytes())))
    return uploads


def _check_event_then_tagmanifest(uploads, prefix: str, event_type: str) -> ET.Element:
    keys = [k for k, _ in uploads]
    events = [i for i, k in enumerate(keys) if "/metadata/premis/" in k and "virus-check" not in k]
    assert len(events) == 1, keys
    assert keys[-1] == f"{prefix}/tagmanifest-sha256.txt"
    event_key, event_body = uploads[events[0]]
    rel = event_key[len(prefix) + 1:]
    tagmanifest = uploads[-1][1].decode()
    import hashlib
    assert f"{hashlib.sha256(event_body).hexdigest()}  {rel}\n" in tagmanifest
    root = ET.fromstring(event_body)
    assert root.findtext(f"{P}eventType") == event_type
    return root


def test_ledger_sync_records_a_metadata_modification_event(monkeypatch) -> None:
    prefix = "dumps/Redump/DVD-Video/Coverdiscs/F/new-pkg"
    uploads = _capture_uploads(monkeypatch, sync)
    plan = sync.sync_bag_from_ledger(
        FakeS3(_bag(prefix)), bucket="b", storage_prefix=prefix,
        ledger_row={"game_key": "new-pkg", "title_slug": "new", "sort_letter": "F"}, execute=True,
    )
    root = _check_event_then_tagmanifest(uploads, prefix, "metadata modification")
    assert "ledger" in root.findtext(f"{P}eventDetailInformation/{P}eventDetail")
    assert any(f.startswith("metadata/premis/ledger-sync-") for f in plan["files"])


def test_ledger_sync_dry_run_uploads_nothing_but_lists_the_event(monkeypatch) -> None:
    prefix = "dumps/x/new-pkg"
    uploads = _capture_uploads(monkeypatch, sync)
    plan = sync.sync_bag_from_ledger(FakeS3(_bag(prefix)), bucket="b", storage_prefix=prefix,
                                     ledger_row={"game_key": "new-pkg", "title_slug": "new", "sort_letter": "F"}, execute=False)
    assert uploads == [] and any("premis/ledger-sync-" in f for f in plan["files"])


def test_press_catalog_patch_records_the_correction(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("patch_press", ROOT / "scripts" / "patch-press-catalog-omeka-and-bag.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    prefix = "press-material/T/tekken-7-01"
    fake = FakeS3(_bag(prefix))
    monkeypatch.setattr(mod, "load_wasabi_settings", lambda p: ({"credentials_file": "c"}, None))
    monkeypatch.setattr(mod, "load_yaml", lambda p: {})
    monkeypatch.setattr(mod, "resolve_config_path", lambda *a: Path("c"))
    monkeypatch.setattr(mod, "make_s3_client", lambda *a: fake)
    monkeypatch.setattr(mod, "wasabi_bucket_for_storage_prefix", lambda *a, **k: "b")
    uploads = _capture_uploads(monkeypatch, mod)
    mod.patch_wasabi_bag(
        ledger={"package_storage_prefix": prefix, "game_key": "tekken-7-01"}, title="Tekken 7",
        reference_uri="https://www.mobygames.com/game/1", former_title="Tekken", correction_summary="title fix",
        execute=True,
    )
    root = _check_event_then_tagmanifest(uploads, prefix, "metadata modification")
    detail = root.findtext(f"{P}eventDetailInformation/{P}eventDetail")
    assert "Tekken" in detail and "Tekken 7" in detail and "title fix" in detail


def test_dvd_menu_backfill_adds_an_event_file_to_the_upload(tmp_path: Path) -> None:
    files: dict[str, Path] = {}
    rel = dvd.record_preview_change(files, tmp_path, package_id="lara-01", kind="contact_sheet",
                                    still=Path("contact_sheet.jpg"), program="apply_dvd_menu_bag_previews.py")
    assert rel.startswith("metadata/premis/dvd-menu-preview-") and files[rel].is_file()
    root = ET.parse(files[rel]).getroot()
    assert root.findtext(f"{P}eventType") == "metadata modification"
    assert "contact sheet" in root.findtext(f"{P}eventDetailInformation/{P}eventDetail")


def test_local_bag_gets_the_same_event_line(tmp_path: Path) -> None:
    bag = tmp_path / "bag"
    bag.mkdir()
    (bag / "tagmanifest-sha256.txt").write_text("aaa  bag-info.txt\n")
    work = tmp_path / "work"
    files: dict[str, Path] = {}
    rel = dvd.record_preview_change(files, work, package_id="p", kind="menu", still=Path("menu_preview.jpg"),
                                    program="apply_dvd_menu_bag_previews.py")
    assert dvd.apply_to_local_bag(files, bag)
    assert (bag / rel).read_bytes() == files[rel].read_bytes()
    assert rel in (bag / "tagmanifest-sha256.txt").read_text()
