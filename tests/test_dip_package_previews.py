"""Preview generation tolerates unreadable payload images."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

# Scripts live on path in CI via package layout; import from repo root.
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dip_package_previews import (  # noqa: E402
    _apply_pillow_pixel_limit,
    _write_grid_thumb,
    write_hitsave_previews,
)


def _write_min_jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color=(128, 64, 32)).save(path, "JPEG")


def test_apply_pillow_pixel_limit_from_config() -> None:
    prior = Image.MAX_IMAGE_PIXELS
    try:
        _apply_pillow_pixel_limit({"max_image_pixels": 536_870_912})
        assert Image.MAX_IMAGE_PIXELS == 536_870_912
        _apply_pillow_pixel_limit({"max_image_pixels": 0})
        assert Image.MAX_IMAGE_PIXELS is None
    finally:
        Image.MAX_IMAGE_PIXELS = prior


def test_truncated_jpeg_skips_grid_thumb_without_raising(tmp_path: Path) -> None:
    good = tmp_path / "data" / "ok.jpg"
    bad = tmp_path / "data" / "trunc.jpg"
    _write_min_jpeg(good)
    _write_min_jpeg(bad)
    raw = bad.read_bytes()
    bad.write_bytes(raw[: max(1, len(raw) // 4)])

    dest = tmp_path / "metadata" / "hitsave" / "thumbnails" / "grid" / "f0.jpg"
    assert _write_grid_thumb(bad, dest, 64, 80) is False
    assert not dest.is_file()


def test_write_hitsave_previews_completes_with_one_bad_image(tmp_path: Path) -> None:
    good = tmp_path / "data" / "ok.jpg"
    bad = tmp_path / "data" / "trunc.jpg"
    _write_min_jpeg(good)
    _write_min_jpeg(bad)
    raw = bad.read_bytes()
    bad.write_bytes(raw[: max(1, len(raw) // 4)])

    manifest = write_hitsave_previews(tmp_path, package_id="test-pkg")
    assert manifest is not None
    assert "f0" in manifest["gallery_file_keys"]
    assert "f1" in manifest["gallery_file_keys"]
    manifest_path = tmp_path / "metadata" / "hitsave" / "preview-manifest.json"
    assert manifest_path.is_file()


def _scan_cfg(**over) -> dict:
    cfg = {
        "gallery_max_bytes": 1000,          # tiny: any real PNG is "too big" for a normal gallery image
        "max_image_pixels": 536_870_912,
        "browse_preview_max_bytes_per_image": 10485760,
        "collage_min_images": 4,
        "collage_max_images": 8,
        "grid_thumbnail_max_edge": 64,
        "grid_jpeg_quality": 80,
        "display_max_edge": 200,
        "display_jpeg_quality": 80,
        "scan_dir_names": ["Scans"],
        "scan_max_bytes": 2147483648,
        "browse_canvas": {"medium": [64, 48], "square": [40, 40]},
        "gallery_mime_types": ["image/jpeg", "image/png"],
        "gallery_extensions": ["jpg", "jpeg", "png"],
    }
    cfg.update(over)
    return cfg


def _noisy_png(path: Path, size: tuple[int, int]) -> None:
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(path, "PNG")


def test_large_scans_get_display_and_grid_and_file_info(tmp_path: Path) -> None:
    _noisy_png(tmp_path / "data" / "game" / "Scans" / "0001.png", (900, 600))
    _noisy_png(tmp_path / "data" / "game" / "cover.png", (900, 600))   # not a scan, over the cap
    (tmp_path / "data" / "game" / "game.xci").write_bytes(b"x" * 10)

    manifest = write_hitsave_previews(tmp_path, package_id="pkg", config=_scan_cfg())

    assert manifest is not None
    keys = manifest["gallery_file_keys"]
    assert len(keys) == 1
    key = keys[0]
    assert manifest["files"][key]["path"] == "data/game/Scans/0001.png"
    assert manifest["files"][key]["scan"] is True
    assert manifest["files"][key]["size"] > 1000
    display = Image.open(tmp_path / manifest["display"][key])
    grid = Image.open(tmp_path / manifest["grid"][key])
    assert max(display.size) == 200 and display.format == "JPEG"
    assert max(grid.size) == 64


def test_scan_folder_match_is_case_insensitive_and_images_only(tmp_path: Path) -> None:
    _noisy_png(tmp_path / "data" / "g" / "scans" / "a.png", (50, 50))
    (tmp_path / "data" / "g" / "scans" / "notes.txt").write_text("hi")
    manifest = write_hitsave_previews(tmp_path, package_id="pkg", config=_scan_cfg())
    assert manifest is not None
    assert [v["path"] for v in manifest["files"].values()] == ["data/g/scans/a.png"]


def test_small_images_still_gallery_without_scan_flag(tmp_path: Path) -> None:
    _write_min_jpeg(tmp_path / "data" / "menu.jpg")
    manifest = write_hitsave_previews(tmp_path, package_id="pkg", config=_scan_cfg(gallery_max_bytes=10_000_000))
    assert manifest is not None
    (info,) = manifest["files"].values()
    assert info["scan"] is False and manifest["display"]


def test_refresh_skips_bags_with_non_payload_previews() -> None:
    import importlib

    pytest = importlib.import_module("pytest")
    pytest.importorskip("psycopg")
    from refresh_bag_previews_wasabi import foreign_preview_keys

    assert foreign_preview_keys(None) == []
    assert foreign_preview_keys({"grid": {"f6": "x", "f7": "y"}, "gallery_file_keys": ["f6", "f7"]}) == []
    assert foreign_preview_keys({"grid": {"menu": "x"}, "gallery_file_keys": ["menu"]}) == ["menu"]
