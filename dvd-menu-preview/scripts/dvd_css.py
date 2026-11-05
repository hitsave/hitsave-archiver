"""Descramble DVD-Video VOB sectors using Redump title keys (libdvdcss algorithm)."""
from __future__ import annotations

import ctypes
import re
import shutil
from pathlib import Path

DVD_KEY_SIZE = 5
SECTOR_SIZE = 2048

_TITLE_KEY_LINE = re.compile(
    r"^(?P<vob>\S+\.VOB)(?:;\d+)?\s+Title\s+Key:\s*(?P<value>.+)$",
    re.MULTILINE,
)

_LIB: ctypes.CDLL | None = None


def _lib() -> ctypes.CDLL:
    global _LIB
    if _LIB is None:
        _LIB = ctypes.CDLL("libhitsave_css_unscramble.so.0")
        _LIB.hitsave_css_unscramble.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
        ]
        _LIB.hitsave_css_unscramble.restype = ctypes.c_int
    return _LIB


def css_protection_disabled(submission_text: str) -> bool:
    return bool(
        re.search(
            r"Copyright Protection System Type:\s*No\b",
            submission_text,
            re.IGNORECASE,
        )
    )


def parse_title_keys(submission_text: str) -> tuple[dict[str, bytes], set[str]]:
    """Return (vob -> key, vobs explicitly marked without a title key)."""
    keys: dict[str, bytes] = {}
    no_key: set[str] = set()
    for m in _TITLE_KEY_LINE.finditer(submission_text):
        vob = m.group("vob").strip().upper().split(";")[0]
        value = m.group("value").strip()
        if re.search(r"no title key", value, re.IGNORECASE):
            no_key.add(vob)
            continue
        try:
            raw = bytes(int(b, 16) for b in value.split())
        except ValueError:
            continue
        if len(raw) != DVD_KEY_SIZE:
            continue
        keys[vob] = raw
    return keys, no_key


def find_submission_info(iso: Path, cfg: dict) -> Path | None:
    name = str(cfg.get("submission_info_filename") or "!submissionInfo.txt")
    sidecar = iso.parent / name
    return sidecar if sidecar.is_file() else None


def menu_vob_candidates(
    keys: dict[str, bytes],
    no_key: set[str],
    *,
    unencrypted_disc: bool,
) -> list[tuple[str, bytes | None]]:
    """Menu VOBs to sample; None key => copy VOB (plaintext or already clear)."""
    names = ("VIDEO_TS.VOB", "VTS_01_0.VOB", "VTS_02_0.VOB")
    out: list[tuple[str, bytes | None]] = []
    for name in names:
        upper = name.upper()
        if upper in keys:
            out.append((name, keys[upper]))
        elif upper in no_key or unencrypted_disc:
            out.append((name, None))
        elif name == "VTS_01_0.VOB" and "VTS_01_1.VOB" in keys:
            # Some Redump sidecars omit _0 but share the title key with _1.
            out.append((name, keys["VTS_01_1.VOB"]))
    return out


def prepare_vob_for_ffmpeg(src: Path, dest: Path, title_key: bytes | None) -> None:
    if title_key is None:
        shutil.copy2(src, dest)
        return
    descramble_vob(src, dest, title_key)


def descramble_vob(src: Path, dest: Path, title_key: bytes) -> None:
    if len(title_key) != DVD_KEY_SIZE:
        raise ValueError("title key must be 5 bytes")
    lib = _lib()
    key_buf = (ctypes.c_uint8 * DVD_KEY_SIZE).from_buffer_copy(title_key)
    sector = bytearray(SECTOR_SIZE)
    c_sector = (ctypes.c_uint8 * SECTOR_SIZE).from_buffer(sector)

    with src.open("rb") as fin, dest.open("wb") as fout:
        while True:
            n = fin.readinto(sector)
            if n == 0:
                break
            if n != SECTOR_SIZE:
                fout.write(sector[:n])
                continue
            if sector[0x14] & 0x30:
                lib.hitsave_css_unscramble(key_buf, c_sector)
            fout.write(sector)


def extract_vob_from_iso(iso: Path, vob_name: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    import subprocess

    inner = f"VIDEO_TS/{vob_name}"
    subprocess.run(
        ["7z", "e", "-y", f"-o{dest.parent}", str(iso), inner],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    extracted = dest.parent / vob_name
    if not extracted.is_file():
        raise FileNotFoundError(f"{inner} not in {iso}")
    if extracted != dest:
        extracted.rename(dest)
