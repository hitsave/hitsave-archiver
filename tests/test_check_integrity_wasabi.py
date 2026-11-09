"""Backfill of damaged-file checks for packages already on Wasabi."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ciw", ROOT / "scripts" / "check-integrity-wasabi.py")
ciw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ciw)

P = "{http://www.loc.gov/premis/v3}"
PREFIX = "press-material/S/some-kit-01"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.effect_noise((200, 150), 64).convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)
        self.puts: list[str] = []

    def get_object(self, Bucket, Key):  # noqa: N803
        return {"Body": io.BytesIO(self.objects[Key])}

    def download_file(self, Bucket, Key, Filename):  # noqa: N803
        Path(Filename).write_bytes(self.objects[Key])

    def put_object(self, Bucket, Key, Body, **kw):  # noqa: N803
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.read()
        self.puts.append(Key)


def _package(tmp_path: Path):
    good = _jpeg_bytes()
    bad = good[: len(good) // 2]
    payload = {"data/Kit/front.jpg": good, "data/Kit/back.jpg": bad, "data/Kit/readme.txt": b"hi\n"}
    inventory = {
        "manifest_version": 1,
        "files": [{"path": p, "size_bytes": len(b), "sha256": _sha(b), "mime": "x"} for p, b in payload.items()],
        "summary": {"file_count": 3, "total_bytes": sum(map(len, payload.values()))},
    }
    inv_bytes = json.dumps(inventory, indent=2).encode()
    manifest = "".join(f"{_sha(b)}  {p}\n" for p, b in payload.items())
    tagmanifest = f"{_sha(inv_bytes)}  metadata/inventory.json\naaa  bag-info.txt\n"
    objects = {f"{PREFIX}/{p}": b for p, b in payload.items()}
    objects.update({
        f"{PREFIX}/metadata/inventory.json": inv_bytes,
        f"{PREFIX}/manifest-sha256.txt": manifest.encode(),
        f"{PREFIX}/tagmanifest-sha256.txt": tagmanifest.encode(),
        f"{PREFIX}/metadata/submission.json": json.dumps({"package_id": "some-kit-01", "package_uuid": "u"}).encode(),
    })
    row = {"game_key": "some-kit-01", "ingest_kind": "press_material", "package_storage_prefix": PREFIX,
           "inventory_json": inventory}
    return FakeS3(objects), row


def test_checkable_files_skips_other_kinds_and_already_checked() -> None:
    inv = {"files": [
        {"path": "data/a.jpg"}, {"path": "data/b.iso"}, {"path": "data/c.pdf", "integrity": {"status": "ok"}},
        {"path": "data/d.mov"},
    ]}
    assert [f["path"] for f in ciw.checkable_files(inv)] == ["data/a.jpg", "data/d.mov"]


def test_merge_integrity_sets_files_and_recounts_summary() -> None:
    import file_integrity as fi
    inv = {"files": [{"path": "data/a.jpg"}, {"path": "data/b.pdf", "integrity": {"status": "ok", "check": "qpdf"}},
                     {"path": "data/c.txt"}], "summary": {"file_count": 3}}
    out = ciw.merge_integrity(inv, {"data/a.jpg": fi.IntegrityResult("damaged", "pillow", "truncated")})
    assert out["files"][0]["integrity"] == {"status": "damaged", "check": "pillow", "detail": "truncated"}
    assert out["summary"]["integrity"] == {"checked": 2, "ok": 1, "damaged": 1, "unchecked": 0}
    assert "integrity" not in inv["files"][0]  # input not mutated


def test_dry_run_checks_but_writes_nothing(tmp_path: Path) -> None:
    s3, row = _package(tmp_path)
    before = dict(s3.objects)
    res = ciw.process_package(s3, row=row, bucket="b", execute=False, workdir=tmp_path)
    assert res["status"] == "would record"
    assert res["counts"] == {"checked": 2, "ok": 1, "damaged": 1, "unchecked": 0}
    assert res["damaged"] == [{"path": "data/Kit/back.jpg", "detail": res["damaged"][0]["detail"]}]
    assert s3.objects == before and s3.puts == []
    assert "inventory_json" not in res


def test_execute_updates_inventory_adds_validation_event_and_tagmanifest_last(tmp_path: Path) -> None:
    s3, row = _package(tmp_path)
    untouched = {k: v for k, v in s3.objects.items() if "/data/" in k or k.endswith(("manifest-sha256.txt", "submission.json"))
                 and "tagmanifest" not in k}
    res = ciw.process_package(s3, row=row, bucket="b", execute=True, workdir=tmp_path)
    assert res["status"] == "recorded"
    assert s3.puts[-1] == f"{PREFIX}/tagmanifest-sha256.txt"
    inv = json.loads(s3.objects[f"{PREFIX}/metadata/inventory.json"])
    by = {f["path"]: f for f in inv["files"]}
    assert by["data/Kit/back.jpg"]["integrity"]["status"] == "damaged"
    assert by["data/Kit/front.jpg"]["integrity"]["status"] == "ok"
    tm = s3.objects[f"{PREFIX}/tagmanifest-sha256.txt"].decode()
    assert f"{_sha(s3.objects[f'{PREFIX}/metadata/inventory.json'])}  metadata/inventory.json" in tm
    events = [k for k in s3.puts if "/metadata/premis/validation-" in k]
    assert len(events) == 1 and events[0][len(PREFIX) + 1:] in tm
    root = ET.fromstring(s3.objects[events[0]])
    assert root.findtext(f"{P}eventOutcomeInformation/{P}eventOutcome") == "fail"
    assert res["inventory_json"]["summary"]["integrity"]["damaged"] == 1
    # payload, payload manifest and submission untouched
    assert all(s3.objects[k] == v for k, v in untouched.items()) and len(untouched) == 5
    assert not any(k in s3.puts for k in untouched)


def test_download_that_doesnt_match_the_manifest_is_refused(tmp_path: Path) -> None:
    s3, row = _package(tmp_path)
    s3.objects[f"{PREFIX}/data/Kit/front.jpg"] = b"changed on wasabi"
    res = ciw.process_package(s3, row=row, bucket="b", execute=True, workdir=tmp_path)
    assert res["status"].startswith("refused") and "front.jpg" in res["status"]
    assert s3.puts == []


def test_package_with_nothing_left_to_check_is_skipped(tmp_path: Path) -> None:
    s3, row = _package(tmp_path)
    for f in row["inventory_json"]["files"]:
        f["integrity"] = {"status": "ok"}
    res = ciw.process_package(s3, row=row, bucket="b", execute=True, workdir=tmp_path)
    assert res["status"] == "skipped: nothing to check" and s3.puts == []


def test_media_is_checked_from_a_presigned_url_without_download(tmp_path: Path, monkeypatch) -> None:
    import file_integrity as fi
    s3, row = _package(tmp_path)
    row["inventory_json"]["files"].append({"path": "data/Kit/trailer.mov", "size_bytes": 10**10, "sha256": "x"})
    seen = {}
    monkeypatch.setattr(fi, "check_media", lambda src: seen.setdefault("src", src) and fi.IntegrityResult("ok", "ffprobe"))
    monkeypatch.setitem(fi.CHECKERS, "media", fi.check_media)
    res = ciw.process_package(s3, row=row, bucket="b", execute=False, workdir=tmp_path,
                              presign=lambda bucket, key: f"https://wasabi.example/{key}?sig")
    assert seen["src"] == f"https://wasabi.example/{PREFIX}/data/Kit/trailer.mov?sig"
    assert res["counts"]["checked"] == 3


def test_recheck_unchecked_picks_up_files_a_missing_tool_left_unchecked() -> None:
    inv = {"files": [{"path": "data/a.pdf", "integrity": {"status": "unchecked", "detail": "no qpdf"}},
                     {"path": "data/b.pdf", "integrity": {"status": "damaged"}}]}
    assert ciw.checkable_files(inv) == []
    assert [f["path"] for f in ciw.checkable_files(inv, recheck_unchecked=True)] == ["data/a.pdf"]


def test_missing_tools_are_reported(monkeypatch) -> None:
    import file_integrity as fi
    monkeypatch.setattr(fi.shutil, "which", lambda name: None if name in ("qpdf", "7zz", "7z") else f"/usr/bin/{name}")
    assert fi.missing_tools() == ["qpdf", "7zz"]


def test_recheck_damaged_picks_up_files_marked_damaged() -> None:
    inv = {"files": [{"path": "data/a.png", "integrity": {"status": "damaged", "detail": "MAX_TEXT_CHUNK"}},
                     {"path": "data/b.png", "integrity": {"status": "ok"}}]}
    assert ciw.checkable_files(inv) == []
    assert [f["path"] for f in ciw.checkable_files(inv, recheck_damaged=True)] == ["data/a.png"]
