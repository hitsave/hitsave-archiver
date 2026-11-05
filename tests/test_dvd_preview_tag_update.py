"""DVD-menu preview backfill must only touch preview files and their tagmanifest lines."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import apply_dvd_menu_bag_previews as dvd

ROOT = "dumps/DVD-Video/Coverdiscs/F/pkg/"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _etag(b: bytes) -> str:  # single-part MD5 ETag (files here are tiny)
    return hashlib.md5(b).hexdigest()


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def upload_file(self, path, bucket, key, Config=None):
        self.objects[key] = Path(path).read_bytes()

    def head_object(self, Bucket, Key):
        b = self.objects[Key]
        return {"ContentLength": len(b), "ETag": f'"{_etag(b)}"'}


def test_upsert_manifest_lines_replaces_and_appends() -> None:
    text = "aaa  bagit.txt\nbbb  metadata/hitsave/browse-medium.png\n"
    out = dvd.upsert_manifest_lines(text, {"metadata/hitsave/browse-medium.png": "new", "metadata/hitsave/x.jpg": "add"})
    assert out == "aaa  bagit.txt\nnew  metadata/hitsave/browse-medium.png\nadd  metadata/hitsave/x.jpg\n"


def test_apply_to_wasabi_is_tag_only(tmp_path: Path, monkeypatch) -> None:
    manifest = b"deadbeef  data/disc.iso\n"
    bag_info = b"Payload-Oxum: 3.1\n"
    tag = f"{_sha(bag_info)}  bag-info.txt\n{_sha(manifest)}  manifest-sha256.txt\nold  metadata/hitsave/browse-medium.png\n"
    s3 = FakeS3({
        ROOT + "manifest-sha256.txt": manifest,
        ROOT + "bag-info.txt": bag_info,
        ROOT + "tagmanifest-sha256.txt": tag.encode(),
        ROOT + "data/disc.iso": b"iso",
    })
    monkeypatch.setattr(dvd, "load_wasabi_settings", lambda p: ({"verify_upload": True, "credentials_file": "x"}, None))
    monkeypatch.setattr(dvd, "resolve_wasabi_bucket", lambda w, k: "hitsave-dumps")
    monkeypatch.setattr(dvd, "require_bool", lambda d, k, where="": True)

    medium = tmp_path / "browse-medium.png"
    medium.write_bytes(b"PNG-new")
    grid = tmp_path / "menu.jpg"
    grid.write_bytes(b"JPG")
    files = {"metadata/hitsave/browse-medium.png": medium, "metadata/hitsave/thumbnails/grid/menu.jpg": grid}

    dvd.apply_to_wasabi(files, ROOT.rstrip("/"), ingest_config_path=tmp_path / "ingest.yaml", s3=s3)

    # Payload fixity baseline and bag-info untouched.
    assert s3.objects[ROOT + "manifest-sha256.txt"] == manifest
    assert s3.objects[ROOT + "bag-info.txt"] == bag_info
    new_tag = s3.objects[ROOT + "tagmanifest-sha256.txt"].decode()
    assert f"{_sha(bag_info)}  bag-info.txt" in new_tag
    assert f"{_sha(manifest)}  manifest-sha256.txt" in new_tag
    assert f"{_sha(b'PNG-new')}  metadata/hitsave/browse-medium.png" in new_tag
    assert f"{_sha(b'JPG')}  metadata/hitsave/thumbnails/grid/menu.jpg" in new_tag
    assert "old  metadata" not in new_tag
    # Every tagmanifest entry still matches the object on "Wasabi".
    for line in new_tag.splitlines():
        digest, rel = line.split(None, 1)
        assert _sha(s3.objects[ROOT + rel]) == digest


def test_apply_to_local_bag_tag_only(tmp_path: Path) -> None:
    bag = tmp_path / "bag"
    (bag / "data").mkdir(parents=True)
    (bag / "manifest-sha256.txt").write_text("deadbeef  data/disc.iso\n")
    (bag / "tagmanifest-sha256.txt").write_text("abc  manifest-sha256.txt\n")
    src = tmp_path / "menu.jpg"
    src.write_bytes(b"JPG")
    assert dvd.apply_to_local_bag({"metadata/hitsave/thumbnails/grid/menu.jpg": src}, bag)
    assert (bag / "manifest-sha256.txt").read_text() == "deadbeef  data/disc.iso\n"
    tag = (bag / "tagmanifest-sha256.txt").read_text()
    assert "abc  manifest-sha256.txt" in tag
    assert f"{_sha(b'JPG')}  metadata/hitsave/thumbnails/grid/menu.jpg" in tag
    assert not dvd.apply_to_local_bag({}, tmp_path / "missing")


def test_menu_backfill_keeps_existing_scans() -> None:
    from apply_dvd_menu_bag_previews import merge_menu_with_existing

    menu = {"grid": {"menu": "metadata/hitsave/thumbnails/grid/menu.jpg"}, "gallery_file_keys": ["menu"],
            "browse": {"medium": "metadata/hitsave/browse-medium.png"}, "dvd_menu_preview": True}
    existing = {
        "grid": {"f4": "metadata/hitsave/thumbnails/grid/f4.jpg", "menu": "old-menu.jpg"},
        "display": {"f4": "metadata/hitsave/display/f4.jpg"},
        "files": {"f4": {"path": "data/x/img124.tif", "size": 5, "scan": True}},
        "gallery_file_keys": ["menu", "f4"],
    }
    merged = merge_menu_with_existing(menu, existing)
    assert merged["gallery_file_keys"] == ["menu", "f4"]
    assert merged["grid"] == {"menu": "metadata/hitsave/thumbnails/grid/menu.jpg",
                              "f4": "metadata/hitsave/thumbnails/grid/f4.jpg"}
    assert merged["display"] == existing["display"] and merged["files"] == existing["files"]
    assert merged["browse"] == menu["browse"]


def test_menu_backfill_without_payload_previews_is_menu_only() -> None:
    from apply_dvd_menu_bag_previews import merge_menu_with_existing

    menu = {"grid": {"menu": "m.jpg"}, "gallery_file_keys": ["menu"]}
    assert merge_menu_with_existing(menu, None) is menu
    assert merge_menu_with_existing(menu, {"grid": {"menu": "old.jpg"}, "gallery_file_keys": ["menu"]}) is menu
