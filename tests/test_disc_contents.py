"""Disc contents: reading data tracks, the contents bag (layout, web copies, viewer keys), re-runs."""
from __future__ import annotations

import json
from pathlib import Path

import bagit
import pytest
from PIL import Image

import build_disc_contents_bag as b
import extract_disc_contents as x
from disc_contents_policy import DiscContentsDecision


def _iso_bytes(label: str = "2002DPK") -> bytes:
    data = bytearray(18 * 2048)
    pvd = 16 * 2048
    data[pvd] = 1
    data[pvd + 1:pvd + 6] = b"CD001"
    data[pvd + 40:pvd + 72] = label.ljust(32).encode()
    return bytes(data)


def test_data_track_from_cue_and_raw_sector_conversion(tmp_path: Path) -> None:
    iso = _iso_bytes()
    raw = b"".join(b"\x00" * 16 + iso[i:i + 2048] + b"\x00" * 288 for i in range(0, len(iso), 2048))
    (tmp_path / "2002DPK.bin").write_bytes(raw)
    (tmp_path / "2002DPK.cue").write_text('FILE "2002DPK.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
    image, mode = x.data_track(tmp_path)
    assert (image.name, mode) == ("2002DPK.bin", "MODE1/2352")
    out = x.to_iso(image, mode, tmp_path / "disc.iso")
    assert out.read_bytes() == iso and x.volume_label(out) == "2002DPK"


def test_audio_first_track_and_iso_preference(tmp_path: Path) -> None:
    (tmp_path / "a.cue").write_text('FILE "t1.bin" BINARY\n  TRACK 01 AUDIO\nFILE "t2.bin" BINARY\n  TRACK 02 MODE2/2352\n')
    assert x.data_track(tmp_path) == (tmp_path / "t2.bin", "MODE2/2352")
    (tmp_path / "disc.iso").write_bytes(_iso_bytes())
    assert x.data_track(tmp_path)[1] == "MODE1/2048"


def test_kinds_and_names() -> None:
    assert [x.kind_of(n) for n in ("a.MPG", "b.doc", "c.pdf", "d.jpg", "e.pcx", "f.exe", "g.hqx", "h.swf")] == [
        "video", "document", "document", "image", "image", "program", "program", "other"]
    assert x.safe_name("2002DPK") == "2002DPK" and x.safe_name(None) == "disc" and x.safe_name("a/b") == "disc"


def test_contents_prefix_mirrors_the_dump() -> None:
    assert b.contents_prefix("dumps/Redump/IBM PC compatible/Multimedia/T/pkg-1/") == \
        "disc-contents/Redump/IBM PC compatible/Multimedia/T/pkg-1"
    with pytest.raises(ValueError):
        b.contents_prefix("press-material/A/x")


def test_staff_decisions_stand_on_rerun() -> None:
    review = DiscContentsDecision("promotional", "review")
    assert b.effective_display(review, "promotional", "public") == "public"  # approved
    assert b.effective_display(review, "promotional", "none") == "none"  # rejected
    assert b.effective_display(review, None, None) == "review"
    retail = DiscContentsDecision("retail", "none")
    assert b.effective_display(retail, "promotional", "public") == "none"  # reclassified: no longer shown


def _jpg(path: Path, size=(320, 240), colour=(120, 40, 200)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, colour)
    for i in range(0, size[0], 7):
        im.putpixel((i, i % size[1]), (255, 255, 255))
    im.save(path, "JPEG")
    return path


def _extracted(tmp_path: Path) -> Path:
    """extract_disc_contents.py output for a tiny press kit."""
    out = tmp_path / "extract"
    files = out / "files" / "2002DPK"
    _jpg(files / "screenshots" / "lotus_ss_1.jpg")
    (files / "movies").mkdir(parents=True)
    (files / "movies" / "robocop.mpg").write_bytes(b"mpeg")
    (files / "press").mkdir()
    (files / "press" / "us_rlh_fact.doc").write_bytes(b"doc")
    (files / "2002DPK.exe").write_bytes(b"MZ")
    d = out / "derived" / "2002DPK"
    (d / "movies").mkdir(parents=True)
    (d / "movies" / "robocop.mpg.mp4").write_bytes(b"mp4")
    _jpg(d / "movies" / "robocop.mpg.sheet.jpg", (1280, 720))
    _jpg(d / "movies" / "robocop.mpg.poster.jpg")
    (d / "press").mkdir()
    (d / "press" / "us_rlh_fact.doc.pdf").write_bytes(b"%PDF-1.4")
    _jpg(d / "press" / "us_rlh_fact.doc.page1.jpg", (495, 640))
    entries = []
    for p in sorted(q for q in files.rglob("*") if q.is_file()):
        rel = p.relative_to(out / "files").as_posix()
        derived = {}
        if rel.endswith(".mpg"):
            derived = {r: f"derived/{rel}.{s}" for r, s in (("access", "mp4"), ("sheet", "sheet.jpg"), ("poster", "poster.jpg"))}
        if rel.endswith(".doc"):
            derived = {"access": f"derived/{rel}.pdf", "page1": f"derived/{rel}.page1.jpg"}
        entries.append({"path": rel, "size": p.stat().st_size, "sha256": x.sha256_of(p), "mime": None,
                        "kind": x.kind_of(p.name), "mtime": "2002-05-16T02:26:42+00:00", "derived": derived})
    (out / "listing.json").write_text(json.dumps({"volume_label": "2002DPK", "root": "2002DPK", "image": "2002DPK.bin",
                                                  "track_mode": "MODE1/2352", "total_files": len(entries),
                                                  "total_bytes": sum(e["size"] for e in entries),
                                                  "by_kind": {}, "files": entries}))
    (out / "result.json").write_text(json.dumps({"status": "ok", "tools": {"ffmpeg": "test"}}))
    return out


SOURCE = {"game_key": "titus-01", "package_id": "titus-01", "bucket": "hitsave-dumps",
          "package_storage_prefix": "dumps/Redump/IBM PC compatible/Multimedia/T/titus-01"}
CFG = {"grid_thumbnail_max_edge": 64, "display_max_edge": 200, "browse_canvas": {},
       "gallery_extensions": ["jpg", "jpeg", "png", "gif", "webp"], "gallery_mime_types": ["image/jpeg"]}


def test_contents_bag_layout_and_web_copies(tmp_path: Path) -> None:
    from dip_package_previews import index_payload_files

    bag_dir = tmp_path / "bag"
    inv = b.build_contents_bag(_extracted(tmp_path), bag_dir, source=SOURCE,
                               decision=DiscContentsDecision("press_kit", "public", ["labelled press kit"]),
                               scan={"files_scanned": 4}, preview_cfg=CFG)
    bagit.Bag(str(bag_dir)).validate()
    assert (bag_dir / "data" / "2002DPK" / "movies" / "robocop.mpg").read_bytes() == b"mpeg"
    keys = {f.relative_path: f.key for f in index_payload_files(bag_dir, "data")}  # the viewer's numbering
    by_path = {f["path"]: f for f in inv["files"]}
    mpg = by_path["data/2002DPK/movies/robocop.mpg"]
    assert mpg["access"] == {"path": f"metadata/hitsave/access/{keys['2002DPK/movies/robocop.mpg']}.mp4",
                             "mime": "video/mp4", "size_bytes": 3}
    assert by_path["data/2002DPK/press/us_rlh_fact.doc"]["access"]["mime"] == "application/pdf"
    assert by_path["data/2002DPK/2002DPK.exe"]["download"] is False  # programs: listed, not downloadable
    manifest = json.loads((bag_dir / "metadata/hitsave/preview-manifest.json").read_text())
    vkey, dkey = keys["2002DPK/movies/robocop.mpg"], keys["2002DPK/press/us_rlh_fact.doc"]
    assert manifest["video_file_keys"] == [vkey] and manifest["document_file_keys"] == [dkey]
    assert manifest["display"][vkey].endswith(f"{vkey}.jpg") and dkey in manifest["grid"]
    assert keys["2002DPK/screenshots/lotus_ss_1.jpg"] in manifest["grid"]  # gallery image previews
    sub = json.loads((bag_dir / "metadata/submission.json").read_text())
    assert sub["classification"]["display"] == "public" and sub["source"]["package_id"] == "titus-01"
    assert "Bag-Group-Identifier: titus-01" in (bag_dir / "bag-info.txt").read_text()


def test_item_package_lookup_skips_the_disc_contents_media() -> None:
    from omeka_dip_media import media_is_dip_package, media_is_disc_contents

    contents = {"o:ingester": "omeka_dip_package",
                "data": {"package_storage_prefix": "disc-contents/Redump/IBM PC compatible/Multimedia/T/x"}}
    dump = {"o:ingester": "omeka_dip_package",
            "data": {"package_storage_prefix": "dumps/Redump/IBM PC compatible/Multimedia/T/x"}}
    assert media_is_disc_contents(contents) and not media_is_disc_contents(dump)
    assert not media_is_dip_package(contents, dip_ingester="omeka_dip_package")
    assert media_is_dip_package(dump, dip_ingester="omeka_dip_package")


def test_one_web_copy_per_clip_from_the_best_encode() -> None:
    videos = {
        "D/Videos/FIBA/NBALIVE08_FIBA_BIG.mov": 63_653_220,
        "D/Videos/FIBA/NBALIVE08_FIBA_HD_720p.mov": 63_700_000,
        "D/Videos/FIBA/NBALIVE08_FIBA_HI.mov": 88_900_000,
        "D/Videos/FIBA/NBALIVE08_FIBA_LO.mov": 8_200_000,
        "D/Videos/Kiki/GO_TO_MOVES_HD_720p.mov": 1,
        "D/Videos/Kiki/KIKIS_GO_TO_MOVES_HD_720p.mov": 2,  # a different clip
        "D/Videos/Other/NBALIVE08_FIBA_LO.mov": 3,  # same name, other folder: its own clip
        "D/Videos/Commercials/Wait_30_7513.mov": 4,
    }
    skips = x.video_variant_skips(videos)
    assert skips == {"D/Videos/FIBA/NBALIVE08_FIBA_BIG.mov", "D/Videos/FIBA/NBALIVE08_FIBA_HI.mov",
                     "D/Videos/FIBA/NBALIVE08_FIBA_LO.mov"}
    # Without a resolution label the largest encode wins.
    assert x.video_variant_skips({"a/clip_LO.mov": 5, "a/clip_BIG.mov": 9, "a/clip_HI.mov": 7}) == {
        "a/clip_LO.mov", "a/clip_HI.mov"}


def test_no_size_cap() -> None:
    import inspect

    assert "max_bytes" not in inspect.signature(x.run).parameters
    assert not hasattr(x, "MAX_BYTES")


def test_data_track_skips_cue_with_missing_file(tmp_path: Path) -> None:
    # The dumper renamed FFVIIAC.bin to its Redump name and left the old cue in the upload.
    (tmp_path / "FFVIIAC.cue").write_text('FILE "FFVIIAC.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    (tmp_path / "Game (USA).cue").write_text('FILE "Game (USA).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    (tmp_path / "Game (USA).bin").write_bytes(b"\0" * 2352)
    assert x.data_track(tmp_path) == (tmp_path / "Game (USA).bin", "MODE2/2352")


def test_contents_bag_records_file_checks(tmp_path: Path) -> None:
    inv = b.build_contents_bag(_extracted(tmp_path), tmp_path / "bag", source=SOURCE,
                               decision=DiscContentsDecision("press_kit", "public", []),
                               scan={"files_scanned": 4}, preview_cfg=CFG)
    by_path = {f["path"]: f for f in inv["files"]}
    assert by_path["data/2002DPK/screenshots/lotus_ss_1.jpg"]["integrity"]["status"] == "ok"
    assert by_path["data/2002DPK/movies/robocop.mpg"]["integrity"]["status"] == "damaged"  # 4 bytes, not video
    assert "integrity" not in by_path["data/2002DPK/2002DPK.exe"]  # no checker for programs
    assert inv["summary"]["integrity"]["damaged"] == 1
    on_disk = json.loads((tmp_path / "bag" / "metadata" / "inventory.json").read_text())
    assert on_disk["summary"]["integrity"] == inv["summary"]["integrity"]
