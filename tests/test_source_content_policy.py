#!/usr/bin/env python3
"""Unit tests for press-material content policy."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from source_content_policy import (  # noqa: E402
    BooruFilenameRules,
    ContentPolicyError,
    check_source_content_policy,
    is_booru_tagged_filename,
    is_hash_named_image_filename,
)

RULES = BooruFilenameRules()


def test_booru_wallpaper_export_detected() -> None:
    name = "128425__amatsuka_haruka__baby_princess__by_jirou__wallpaper.jpg"
    assert is_booru_tagged_filename(name, rules=RULES)


def test_booru_multi_tag_without_wallpaper_suffix() -> None:
    name = "214885__-__bungaku_shoujo__hinosaka_nano__seifuku__takeoka_miho__wallpaper.jpg"
    assert is_booru_tagged_filename(name, rules=RULES)


def test_press_kit_names_not_flagged() -> None:
    rules = RULES
    for name in (
        "screenshot_01.jpg",
        "Logo_Final.png",
        "fact-sheet.pdf",
        "Baby_Princess_key_art.jpg",
        "128425_cover.jpg",
    ):
        assert not is_booru_tagged_filename(name, rules=rules)


def test_enforce_blocks_folder(tmp_path: Path) -> None:
    folder = tmp_path / "game"
    folder.mkdir()
    (folder / "999__tag_a__tag_b__tag_c__wallpaper.jpg").write_bytes(b"x")
    cfg = {"content_policy": {"enforce": True, "blockers": {"booru_tagged_filenames": {"enabled": True}}}}
    try:
        check_source_content_policy(folder, cfg)
        raise AssertionError("expected ContentPolicyError")
    except ContentPolicyError as e:
        assert e.code == "booru_tagged_filenames"
        assert len(e.matches) == 1


def test_hash_named_avatar_style_wallpaper() -> None:
    assert is_hash_named_image_filename("8fa9e6b3c06dcf6e3bca8a0f821d6324.jpg")


def test_hash_named_blocks_single_image_sip(tmp_path: Path) -> None:
    folder = tmp_path / "Avatar_The_Last_Airbender"
    folder.mkdir()
    (folder / "8fa9e6b3c06dcf6e3bca8a0f821d6324.jpg").write_bytes(b"x")
    cfg = {
        "content_policy": {
            "enforce": True,
            "blockers": {
                "booru_tagged_filenames": {"enabled": False},
                "hash_named_images": {"enabled": True},
            },
        }
    }
    try:
        check_source_content_policy(folder, cfg)
        raise AssertionError("expected ContentPolicyError")
    except ContentPolicyError as e:
        assert e.code == "hash_named_images"


def test_hash_named_does_not_block_mixed_press_kit(tmp_path: Path) -> None:
    folder = tmp_path / "game"
    folder.mkdir()
    (folder / "8fa9e6b3c06dcf6e3bca8a0f821d6324.jpg").write_bytes(b"x")
    (folder / "fact_sheet.pdf").write_bytes(b"x")
    (folder / "cover_art_final.jpg").write_bytes(b"x")
    cfg = {
        "content_policy": {
            "enforce": True,
            "blockers": {"hash_named_images": {"enabled": True}},
        }
    }
    check_source_content_policy(folder, cfg)


def test_enforce_false_warns_only(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "game"
        folder.mkdir()
        (folder / "999__tag_a__tag_b__tag_c__wallpaper.jpg").write_bytes(b"x")
        cfg = {"content_policy": {"enforce": False, "blockers": {"booru_tagged_filenames": {"enabled": True}}}}
        check_source_content_policy(folder, cfg)
        out = capsys.readouterr().out
        assert "warn only" in out
