#!/usr/bin/env python3
"""Generate synthetic submission security test zips (safe, small, for CI/local)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "submissions-security"


def write_allowlist_fail() -> None:
    path = OUT / "allowlist-exe.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("game/readme.txt", "ok")
        zf.writestr("game/run.exe", b"MZfake")
    print(f"Wrote {path}")


def write_nested_archive() -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("inner.txt", b"nested")
    path = OUT / "nested-inner-zip.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("payload.zip", inner.getvalue())
    print(f"Wrote {path}")


def write_extract_bytes_cap() -> None:
    """Single file larger than ci-tiny-limits.yaml max_extracted_bytes (stream cap test)."""
    path = OUT / "extract-bytes-cap.zip"
    payload = b"\xff\xd8\xff" + (b"\x00" * 4096)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("game/small.jpg", payload)
    print(f"Wrote {path} ({len(payload)} byte payload)")


def write_valid_minimal() -> None:
    path = OUT / "valid-minimal.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Topatoi/readme.txt", "press sample")
        zf.writestr("Topatoi/shot.jpg", b"\xff\xd8\xff\xd8" + b"\x00" * 8)
    print(f"Wrote {path}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_valid_minimal()
    write_allowlist_fail()
    write_nested_archive()
    write_extract_bytes_cap()


if __name__ == "__main__":
    main()
