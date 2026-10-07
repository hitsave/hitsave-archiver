#!/usr/bin/env python3
"""Unit tests for portable submission zip helpers."""
from __future__ import annotations

import io
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from submission_common import (  # noqa: E402
    SubmissionError,
    SubmissionLimits,
    SubmissionSecurity,
    extract_zip_safely,
    inspect_zip,
    resolve_source_folder,
    slug_game_key,
    validate_allowlist,
)

LIMITS = SubmissionLimits(
    max_zip_bytes=1_000_000,
    max_uncompressed_bytes=2_000_000,
    max_extracted_bytes=2_000_000,
    max_archive_members=100,
    max_path_length=200,
    max_member_bytes=500_000,
    max_manifest_bytes=4096,
)

SECURITY = SubmissionSecurity(
    reject_nested_archives=True,
    nested_archive_extensions=frozenset({".zip", ".7z"}),
    allowed_extensions=frozenset({".jpg", ".txt", ".yaml"}),
)


def _make_zip(members: dict[str, bytes]) -> Path:
    tmp = Path(tempfile.mkdtemp())
    zpath = tmp / "test.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return zpath


def _make_symlink_zip() -> Path:
    tmp = Path(tempfile.mkdtemp())
    zpath = tmp / "symlink.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        info = zipfile.ZipInfo("link-to-secret")
        info.create_system = 3
        info.external_attr = (0o120000 | 0o777) << 16
        zf.writestr(info, b"target")
    return zpath


def test_slug_game_key():
    key = slug_game_key("My Game!!!")
    assert key.startswith("submission-")
    assert "my-game" in key


def test_resolve_single_top_level_folder():
    root = Path(tempfile.mkdtemp())
    (root / "only").mkdir()
    assert resolve_source_folder(root).name == "only"


def test_extract_and_resolve():
    z = _make_zip({"game/readme.txt": b"hello", "game/img/a.jpg": b"\x00"})
    dest = Path(tempfile.mkdtemp()) / "out"
    extract_zip_safely(z, dest, LIMITS, SECURITY)
    assert resolve_source_folder(dest).name == "game"


def test_rejects_zip_slip():
    z = _make_zip({"../evil.txt": b"x"})
    dest = Path(tempfile.mkdtemp()) / "out"
    try:
        extract_zip_safely(z, dest, LIMITS, SECURITY)
        raised = False
    except SubmissionError:
        raised = True
    assert raised


def test_inspect_zip_member_limit():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(5):
            zf.writestr(f"f{i}.txt", b"a")
    path = Path(tempfile.mkdtemp()) / "many.zip"
    path.write_bytes(buf.getvalue())
    tiny = SubmissionLimits(99999, 99999, 99999, 3, 200, 99999, 4096)
    try:
        inspect_zip(path, tiny, SECURITY)
        raised = False
    except SubmissionError:
        raised = True
    assert raised


def test_rejects_nested_zip_member():
    z = _make_zip({"payload.zip": b"PK"})
    dest = Path(tempfile.mkdtemp()) / "out"
    try:
        extract_zip_safely(z, dest, LIMITS, SECURITY)
        raised = False
    except SubmissionError:
        raised = True
    assert raised


def test_rejects_symlink_zip_entry():
    z = _make_symlink_zip()
    dest = Path(tempfile.mkdtemp()) / "out"
    try:
        extract_zip_safely(z, dest, LIMITS, SECURITY)
        raised = False
    except SubmissionError:
        raised = True
    assert raised


def test_allowlist_rejects_exe():
    root = Path(tempfile.mkdtemp())
    (root / "a.jpg").write_bytes(b"\xff")
    (root / "bad.exe").write_bytes(b"MZ")
    try:
        validate_allowlist(root, SECURITY.allowed_extensions)
        raised = False
    except SubmissionError:
        raised = True
    assert raised


def test_streamed_member_cap():
    z = _make_zip({"big.bin": b"x" * 600_000})
    tiny_limits = SubmissionLimits(
        max_zip_bytes=2_000_000,
        max_uncompressed_bytes=2_000_000,
        max_extracted_bytes=2_000_000,
        max_archive_members=100,
        max_path_length=200,
        max_member_bytes=100_000,
        max_manifest_bytes=4096,
    )
    dest = Path(tempfile.mkdtemp()) / "out"
    try:
        extract_zip_safely(z, dest, tiny_limits, SECURITY)
        raised = False
    except SubmissionError:
        raised = True
    assert raised


if __name__ == "__main__":
    test_slug_game_key()
    test_resolve_single_top_level_folder()
    test_extract_and_resolve()
    test_rejects_zip_slip()
    test_inspect_zip_member_limit()
    test_rejects_nested_zip_member()
    test_rejects_symlink_zip_entry()
    test_allowlist_rejects_exe()
    test_streamed_member_cap()
    print("ok")
