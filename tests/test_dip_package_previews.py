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
