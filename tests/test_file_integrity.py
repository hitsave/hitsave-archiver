"""Damaged-file checks: recorded, never blocking. Fixtures are made on the fly and then truncated."""
from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

import file_integrity as fi

HAVE_QPDF = shutil.which("qpdf") is not None
HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAVE_7Z = shutil.which("7zz") is not None or shutil.which("7z") is not None


def _truncate(path: Path, keep: float = 0.5) -> Path:
    data = path.read_bytes()
    out = path.with_name("cut-" + path.name)
    out.write_bytes(data[: int(len(data) * keep)])
    return out


def _jpeg(tmp_path: Path) -> Path:
    path = tmp_path / "scan.jpg"
    Image.effect_noise((400, 300), 64).convert("RGB").save(path, quality=90)
    return path


def test_kind_comes_from_extension() -> None:
    assert fi.kind_for(Path("a/Front.JPG")) == "image"
    assert fi.kind_for(Path("a/b.tif")) == "image"
    assert fi.kind_for(Path("press.pdf")) == "pdf"
    assert fi.kind_for(Path("trailer.MOV")) == "media"
    assert fi.kind_for(Path("music.mp3")) == "media"
    assert fi.kind_for(Path("assets.zip")) == "archive"
    assert fi.kind_for(Path("disc.iso")) is None
    assert fi.kind_for(Path("notes.txt")) is None


def test_files_with_no_checker_get_no_result(tmp_path: Path) -> None:
    (tmp_path / "disc.iso").write_bytes(b"x")
    assert fi.check_file(tmp_path / "disc.iso") is None


def test_good_jpeg_is_ok_and_truncated_jpeg_is_damaged(tmp_path: Path) -> None:
    good = _jpeg(tmp_path)
    assert fi.check_file(good).status == "ok"
    bad = fi.check_file(_truncate(good))
    assert bad.status == "damaged" and bad.check == "pillow"
    assert "truncated" in bad.detail


def test_truncation_is_caught_even_when_previews_allow_truncated_loads(tmp_path: Path) -> None:
    from PIL import Image as PILImage, ImageFile
    import dip_package_previews  # noqa: F401  (sets LOAD_TRUNCATED_IMAGES = True on import)
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    before = PILImage.MAX_IMAGE_PIXELS
    assert fi.check_file(_truncate(_jpeg(tmp_path))).status == "damaged"
    assert ImageFile.LOAD_TRUNCATED_IMAGES is True and PILImage.MAX_IMAGE_PIXELS == before


def test_png_with_bad_crc_is_damaged(tmp_path: Path) -> None:
    path = tmp_path / "logo.png"
    Image.new("RGB", (50, 50), "red").save(path)
    data = bytearray(path.read_bytes())
    data[40] ^= 0xFF  # inside the IDAT chunk
    path.write_bytes(bytes(data))
    assert fi.check_file(path).status == "damaged"


def test_not_an_image_at_all_is_damaged(tmp_path: Path) -> None:
    path = tmp_path / "front.jpg"
    path.write_bytes(b"<html>404 Not Found</html>")
    res = fi.check_file(path)
    assert res.status == "damaged" and res.detail


def test_huge_image_is_unchecked_not_decoded(tmp_path: Path, monkeypatch) -> None:
    good = _jpeg(tmp_path)
    monkeypatch.setattr(fi, "MAX_IMAGE_PIXELS", 1000)
    res = fi.check_file(good)
    assert res.status == "unchecked" and "pixels" in res.detail


@pytest.mark.skipif(not HAVE_QPDF, reason="qpdf not installed")
def test_good_pdf_is_ok_and_truncated_pdf_is_damaged(tmp_path: Path) -> None:
    pdf = tmp_path / "press-release.pdf"
    pages = [Image.effect_noise((300, 300), 40).convert("RGB") for _ in range(3)]
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    assert fi.check_file(pdf).status == "ok"
    bad = fi.check_file(_truncate(pdf, 0.4))
    assert bad.status == "damaged" and bad.check == "qpdf"


@pytest.mark.skipif(not HAVE_QPDF, reason="qpdf not installed")
def test_pdf_missing_only_its_trailer_is_damaged(tmp_path: Path) -> None:
    pdf = tmp_path / "flyer.pdf"
    Image.effect_noise((300, 300), 40).convert("RGB").save(pdf)
    cut = tmp_path / "cut.pdf"
    cut.write_bytes(pdf.read_bytes()[:-200])
    assert fi.check_file(cut).status == "damaged"


@pytest.mark.skipif(not HAVE_QPDF, reason="qpdf not installed")
def test_pdf_with_only_wrong_xref_offsets_is_ok_but_noted(tmp_path: Path) -> None:
    pdf = tmp_path / "old.pdf"
    Image.effect_noise((100, 100), 40).convert("RGB").save(pdf)
    data = pdf.read_bytes()
    pdf.write_bytes(b"%PDF-1.4\n%padding-shifts-every-offset\n" + data[len(b"%PDF-1.4\n"):])
    res = fi.check_file(pdf)
    assert res.status == "ok" and res.detail.startswith("recoverable")


@pytest.mark.skipif(not HAVE_QPDF, reason="qpdf not installed")
def test_detail_does_not_leak_the_local_path(tmp_path: Path) -> None:
    pdf = tmp_path / "flyer.pdf"
    Image.effect_noise((300, 300), 40).convert("RGB").save(pdf)
    cut = tmp_path / "cut.pdf"
    cut.write_bytes(pdf.read_bytes()[:-200])
    res = fi.check_file(cut)
    assert str(tmp_path) not in res.detail and res.detail


@pytest.mark.skipif(not HAVE_QPDF, reason="qpdf not installed")
def test_long_paths_are_removed_before_the_detail_is_shortened(tmp_path: Path) -> None:
    deep = tmp_path / ("x" * 120) / ("y" * 120)
    deep.mkdir(parents=True)
    pdf = deep / "flyer.pdf"
    Image.effect_noise((300, 300), 40).convert("RGB").save(pdf)
    cut = deep / "cut.pdf"
    cut.write_bytes(pdf.read_bytes()[:-200])
    res = fi.check_file(cut)
    assert "xxxx" not in res.detail and "yyyy" not in res.detail


def test_signed_url_is_shown_as_the_file_name() -> None:
    url = "https://s3.wasabisys.com/b/press/My%20Trailer.mov?X-Amz-Signature=abc"
    assert fi._display_name(url) == "My Trailer.mov"


@pytest.mark.skipif(not HAVE_QPDF, reason="qpdf not installed")
def test_text_renamed_to_pdf_is_damaged(tmp_path: Path) -> None:
    path = tmp_path / "fake.pdf"
    path.write_text("not a pdf")
    assert fi.check_file(path).status == "damaged"


@pytest.mark.skipif(not HAVE_7Z, reason="7-Zip not installed")
def test_good_zip_is_ok_and_truncated_zip_is_damaged(tmp_path: Path) -> None:
    path = tmp_path / "assets.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(5):
            zf.writestr(f"f{i}.bin", bytes(range(256)) * 400)
    assert fi.check_file(path).status == "ok"
    bad = fi.check_file(_truncate(path))
    assert bad.status == "damaged" and bad.check == "7z"


@pytest.mark.skipif(not HAVE_7Z, reason="7-Zip not installed")
def test_password_protected_zip_is_unchecked(tmp_path: Path) -> None:
    src = tmp_path / "secret.txt"
    src.write_text("x" * 1000)
    path = tmp_path / "locked.zip"
    seven = shutil.which("7zz") or shutil.which("7z")
    subprocess.run([seven, "a", "-psecret", str(path), str(src)], check=True, capture_output=True)
    res = fi.check_file(path)
    assert res.status == "unchecked" and "password" in res.detail.lower()


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_good_video_is_ok_and_truncated_video_is_damaged(tmp_path: Path) -> None:
    path = tmp_path / "trailer.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    assert fi.check_file(path).status == "ok"
    bad = fi.check_file(_truncate(path))
    assert bad.status == "damaged" and bad.check in ("ffprobe", "ffmpeg")


def test_a_checker_crash_is_unchecked_not_raised(tmp_path: Path, monkeypatch) -> None:
    good = _jpeg(tmp_path)
    def boom(path):
        raise RuntimeError("tool exploded")
    monkeypatch.setitem(fi.CHECKERS, "image", boom)
    res = fi.check_file(good)
    assert res.status == "unchecked" and "tool exploded" in res.detail


def test_record_drops_empty_fields() -> None:
    assert fi.IntegrityResult("ok", "pillow").record() == {"status": "ok", "check": "pillow"}
    assert fi.IntegrityResult("damaged", "qpdf", "xref not found").record() == {
        "status": "damaged", "check": "qpdf", "detail": "xref not found"}


def test_summary_counts_and_lists_damaged() -> None:
    results = {"a.jpg": fi.IntegrityResult("ok", "pillow"), "b.pdf": fi.IntegrityResult("damaged", "qpdf", "bad"),
               "c.zip": fi.IntegrityResult("unchecked", "7z", "password")}
    assert fi.summarize(results) == {"checked": 3, "ok": 1, "damaged": 1, "unchecked": 1}


def _build(tmp_path: Path, monkeypatch, sip: Path):
    import importlib.util
    import xml.etree.ElementTree as ET  # noqa: F401
    import format_identification as fid
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("bbp_integrity", root / "scripts" / "build-bagit-package.py")
    bbp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bbp)
    identity = type("I", (), {})()
    for k, v in dict(package_id="pkg-1", package_uuid="uuid-1", relative_sip_path=sip.name, sip_fingerprint="f",
                     title_slug="t", sort_letter="S", omeka_item_title="T").items():
        setattr(identity, k, v)
    identity.external_identifier = lambda: "hitsave:pkg-1"
    monkeypatch.setattr(bbp, "load_packaging_config", lambda: {"bagit": {}})
    monkeypatch.setattr(bbp, "load_ingest_config", lambda: {"wasabi": {}})
    monkeypatch.setattr(bbp, "resolve_submission_source", lambda s: None)
    monkeypatch.setattr(bbp, "resolve_wasabi_bucket", lambda w, k: "b")
    monkeypatch.setattr(bbp, "load_preview_config", lambda: {})
    monkeypatch.setattr(bbp, "write_hitsave_previews", lambda *a, **k: None)
    monkeypatch.setattr(bbp, "identify_tree", lambda r: fid.FormatRun(status="disabled"))
    bag = tmp_path / "bag"
    inventory, _ = bbp.build_bagit_package(sip, bag, identity=identity, agent_version="t", repository_code="hitsave",
                                           max_files=10, max_bytes=0)
    return bag, inventory


def test_built_bag_keeps_damaged_files_and_records_them(tmp_path: Path, monkeypatch) -> None:
    import xml.etree.ElementTree as ET
    import bagit
    sip = tmp_path / "Press Kit"
    sip.mkdir()
    good = _jpeg(sip)
    bad = sip / "back.jpg"
    bad.write_bytes(good.read_bytes()[: good.stat().st_size // 2])
    (sip / "readme.txt").write_text("hello\n")
    bag, inventory = _build(tmp_path, monkeypatch, sip)
    by_name = {Path(f["path"]).name: f for f in inventory["files"]}
    assert by_name["scan.jpg"]["integrity"] == {"status": "ok", "check": "pillow"}
    assert by_name["back.jpg"]["integrity"]["status"] == "damaged"
    assert "integrity" not in by_name["readme.txt"]
    assert (bag / "data" / "back.jpg").read_bytes() == bad.read_bytes()  # preserved as received
    assert inventory["summary"]["integrity"] == {"checked": 2, "ok": 1, "damaged": 1, "unchecked": 0}
    events = [ET.parse(p).getroot() for p in (bag / "metadata" / "premis").glob("validation-*.xml")]
    assert len(events) == 1
    P = "{http://www.loc.gov/premis/v3}"
    assert events[0].findtext(f"{P}eventOutcomeInformation/{P}eventOutcome") == "fail"
    assert "back.jpg" in events[0].findtext(f"{P}eventOutcomeInformation/{P}eventOutcomeDetailNote")
    bagit.Bag(str(bag)).validate()


def test_bag_with_nothing_to_check_has_no_validation_event(tmp_path: Path, monkeypatch) -> None:
    sip = tmp_path / "Dump"
    sip.mkdir()
    (sip / "disc.iso").write_bytes(b"iso")
    bag, inventory = _build(tmp_path, monkeypatch, sip)
    assert "integrity" not in inventory["summary"]
    assert not list((bag / "metadata" / "premis").glob("validation-*.xml"))


def test_png_with_a_large_metadata_chunk_is_not_damaged(tmp_path: Path) -> None:
    import os
    from PIL import PngImagePlugin
    info = PngImagePlugin.PngInfo()
    info.add_text("XML:com.adobe.xmp", os.urandom(800_000).hex(), zip=True)  # ~1.6 MB once inflated
    path = tmp_path / "logo.png"
    Image.new("RGB", (40, 40), "blue").save(path, pnginfo=info)
    before = (PngImagePlugin.MAX_TEXT_CHUNK, PngImagePlugin.MAX_TEXT_MEMORY)
    assert fi.check_file(path).status == "ok"
    assert (PngImagePlugin.MAX_TEXT_CHUNK, PngImagePlugin.MAX_TEXT_MEMORY) == before


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_image_pillow_cannot_identify_is_ok_when_ffmpeg_decodes_it(tmp_path: Path, monkeypatch) -> None:
    # A valid image Pillow has no mode for (TIFFs now go to libtiff first, so a WebP stands in).
    from PIL import Image as PILImage, UnidentifiedImageError
    path = tmp_path / "odd.webp"
    PILImage.new("RGB", (32, 32), "green").save(path)
    real_open = PILImage.open
    def refuse(fp, *a, **k):
        raise UnidentifiedImageError("cannot identify image file")
    monkeypatch.setattr(PILImage, "open", refuse)
    res = fi.check_file(path)
    monkeypatch.setattr(PILImage, "open", real_open)
    assert res.status == "ok" and res.check == "ffmpeg"
    assert "Pillow" in res.detail


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_html_saved_as_jpg_is_still_damaged(tmp_path: Path) -> None:
    path = tmp_path / "deadbang.jpg"
    path.write_text('<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML 2.0//EN"><html><body>404</body></html>')
    res = fi.check_file(path)
    assert res.status == "damaged"


HAVE_TIFFINFO = shutil.which("tiffinfo") is not None


def _cmyka_tiff(path: Path, width: int = 64, height: int = 48) -> Path:
    """An uncompressed CMYK+alpha TIFF (5 samples, one strip per 8 rows): valid, but Pillow
    has no mode for it. Written by hand because nothing in the image can write one."""
    import struct
    rows_per_strip, spp = 8, 5
    strip_bytes = [min(rows_per_strip, height - y) * width * spp for y in range(0, height, rows_per_strip)]
    pixels = bytes((i * 7) % 251 for i in range(sum(strip_bytes)))
    n_strips = len(strip_bytes)
    entries = 13
    ifd_at = 8
    extra_at = ifd_at + 2 + entries * 12 + 4
    bps_at = extra_at
    offsets_at = bps_at + 2 * spp
    counts_at = offsets_at + 4 * n_strips
    data_at = counts_at + 4 * n_strips
    offsets, pos = [], data_at
    for n in strip_bytes:
        offsets.append(pos)
        pos += n
    def entry(tag, typ, count, value):
        return struct.pack("<HHII", tag, typ, count, value)
    ifd = [
        entry(256, 4, 1, width), entry(257, 4, 1, height),
        entry(258, 3, spp, bps_at), entry(259, 3, 1, 1), entry(262, 3, 1, 5),
        entry(273, 4, n_strips, offsets_at), entry(277, 3, 1, spp), entry(278, 4, 1, rows_per_strip),
        entry(279, 4, n_strips, counts_at), entry(284, 3, 1, 1), entry(332, 3, 1, 1),
        entry(338, 3, 1, 2), entry(339, 3, 1, 1),
    ]
    assert len(ifd) == entries
    blob = (b"II*\x00" + struct.pack("<I", ifd_at) + struct.pack("<H", entries) + b"".join(ifd)
            + struct.pack("<I", 0) + struct.pack("<" + "H" * spp, *([8] * spp))
            + struct.pack("<" + "I" * n_strips, *offsets) + struct.pack("<" + "I" * n_strips, *strip_bytes)
            + pixels)
    path.write_bytes(blob)
    return path


@pytest.mark.skipif(not HAVE_TIFFINFO, reason="libtiff tools not installed")
def test_cmyk_alpha_tiff_is_ok_through_libtiff(tmp_path: Path) -> None:
    res = fi.check_file(_cmyka_tiff(tmp_path / "30_many_hd.tif"))
    assert res == fi.IntegrityResult("ok", "libtiff")


@pytest.mark.skipif(not HAVE_TIFFINFO, reason="libtiff tools not installed")
def test_cut_off_tiff_is_damaged_with_the_libtiff_message(tmp_path: Path) -> None:
    res = fi.check_file(_truncate(_cmyka_tiff(tmp_path / "scan.tif"), keep=0.7))
    assert res.status == "damaged" and res.check == "libtiff"
    assert "strip" in res.detail.lower()
    assert str(tmp_path) not in res.detail


@pytest.mark.skipif(not HAVE_TIFFINFO, reason="libtiff tools not installed")
def test_corrupt_compressed_tiff_is_damaged(tmp_path: Path) -> None:
    # The image must compress: zeros written into raw (stored) data can't be told apart
    # from pixels by any tool.
    path = tmp_path / "scan.tiff"
    Image.linear_gradient("L").resize((600, 400)).convert("RGB").save(path, compression="tiff_adobe_deflate")
    data = bytearray(path.read_bytes())
    mid = len(data) // 2
    data[mid:mid + 64] = bytes(64)
    path.write_bytes(bytes(data))
    assert fi.check_file(path).status == "damaged"


@pytest.mark.skipif(not HAVE_TIFFINFO, reason="libtiff tools not installed")
def test_text_renamed_to_tif_is_damaged(tmp_path: Path) -> None:
    path = tmp_path / "scan.TIF"
    path.write_text("not a tiff")
    res = fi.check_file(path)
    assert res.status == "damaged" and res.check == "libtiff"


@pytest.mark.skipif(not HAVE_TIFFINFO, reason="libtiff tools not installed")
def test_tiff_over_the_pixel_cap_is_still_checked(tmp_path: Path, monkeypatch) -> None:
    # libtiff decodes one strip at a time, so a huge scan needs no pixel cap.
    monkeypatch.setattr(fi, "MAX_IMAGE_PIXELS", 100)
    assert fi.check_file(_cmyka_tiff(tmp_path / "poster.tif")).status == "ok"


def test_tiff_falls_back_to_pillow_without_libtiff_tools(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "scan.tif"
    Image.new("RGB", (32, 32), "green").save(path)
    real_which = shutil.which
    monkeypatch.setattr(fi.shutil, "which", lambda name: None if name == "tiffinfo" else real_which(name))
    assert fi.check_file(path) == fi.IntegrityResult("ok", "pillow")


def test_missing_tools_names_tiffinfo(monkeypatch) -> None:
    monkeypatch.setattr(fi.shutil, "which", lambda name: None)
    assert "tiffinfo" in fi.missing_tools()
