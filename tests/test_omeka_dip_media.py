#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from omeka_dip_media import media_is_dip_package  # noqa: E402


def test_dip_package_renderer() -> None:
    body = {"o:renderer": "omeka_dip_package", "o:media_type": "application/x-tar"}
    assert media_is_dip_package(body, dip_ingester="omeka_dip_package")


def test_wasabi_s3_prefix_counts_as_dip() -> None:
    body = {
        "o:renderer": "omeka_dip_package",
        "data": {
            "package_storage_prefix": "press-material/F/game-id",
            "dip_storage": "wasabi-s3",
        },
    }
    assert media_is_dip_package(body, dip_ingester="omeka_dip_package")


def test_png_is_not_dip() -> None:
    body = {
        "o:renderer": "file",
        "o:media_type": "image/png",
        "data": {"original_filename": "cover.png"},
    }
    assert not media_is_dip_package(body, dip_ingester="omeka_dip_package")


def test_stray_tar_without_dip_ingester_not_dip() -> None:
    body = {
        "o:renderer": "file",
        "o:media_type": "application/x-tar",
        "data": {"original_filename": "notes.tar"},
    }
    assert not media_is_dip_package(body, dip_ingester="omeka_dip_package")
