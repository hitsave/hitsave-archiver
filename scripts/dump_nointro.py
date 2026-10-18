#!/usr/bin/env python3
# Parse No-Intro / dumping.guide submission text sidecars.
# System labels: DAT-o-MATIC dat names (e.g. Nintendo - Nintendo 64), https://datomatic.no-intro.org/
from __future__ import annotations

import re
from dataclasses import dataclass, field

_ROM_SECTION = re.compile(r"^--\s*ROM\(s\)/file\(s\)\s*--\s*$", re.I)
_FIELD = re.compile(r"^([^:\n]+):\s*(.*)$")

_SIZE_KEYS = {"size (bytes)", "size"}
_CRC_KEYS = {"crc32"}
_MD5_KEYS = {"md5 hash", "md5"}
_SHA1_KEYS = {"sha-1 hash", "sha-1", "sha1 hash", "sha1"}
_SHA256_KEYS = {"sha-256 hash", "sha-256", "sha256 hash", "sha256"}

# Prefer explicit System (DAT-o-MATIC) over dumping.guide Platform.
_SYSTEM_FIELD_KEYS = (
    "system",
    "no-intro system",
    "dat-o-matic system",
    "platform",
    "rom system",
    "console",
)
_GAME_NAME_KEYS = {"game name", "title"}
_NOINTRO_RECORD_KEYS = {"no-intro record", "no-intro record id", "datomatic record", "record id"}


def _norm_key(key: str) -> str:
    return " ".join((key or "").strip().split()).casefold()


def _normalize_hex(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower().replace(" ", "")
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    return cleaned or None


@dataclass(frozen=True)
class NoIntroRomChecksum:
    size: int
    crc32: str | None = None
    md5: str | None = None
    sha1: str | None = None
    sha256: str | None = None
    filename: str | None = None


@dataclass
class NoIntroSubmission:
    fields: dict[str, str] = field(default_factory=dict)
    roms: list[NoIntroRomChecksum] = field(default_factory=list)

    @property
    def game_name(self) -> str | None:
        for key in _GAME_NAME_KEYS:
            val = (self.fields.get(key) or "").strip()
            if val:
                return val
        return None

    @property
    def dat_system(self) -> str | None:
        """DAT-o-MATIC system label (e.g. Nintendo - Nintendo 3DS)."""
        for key in _SYSTEM_FIELD_KEYS:
            val = (self.fields.get(key) or "").strip()
            if val and not val.startswith("("):
                return val
        return None

    @property
    def platform(self) -> str | None:
        """Alias for :pyattr:`dat_system` (dumping.guide ``Platform`` line)."""
        return self.dat_system

    @property
    def datomatic_record_id(self) -> str | None:
        for key in _NOINTRO_RECORD_KEYS:
            val = (self.fields.get(key) or "").strip()
            if val:
                return val
        return None


def _flush_rom(rom_buf: dict[str, str], roms: list[NoIntroRomChecksum]) -> None:
    if not rom_buf:
        return
    size_raw = ""
    for k in _SIZE_KEYS:
        if k in rom_buf:
            size_raw = rom_buf[k]
            break
    size_digits = size_raw.replace(",", "").strip()
    if not size_digits.isdigit():
        return
    crc = md5 = sha1 = sha256 = None
    for k, v in rom_buf.items():
        if k in _CRC_KEYS:
            crc = _normalize_hex(v)
        elif k in _MD5_KEYS:
            md5 = _normalize_hex(v)
        elif k in _SHA1_KEYS:
            sha1 = _normalize_hex(v)
        elif k in _SHA256_KEYS:
            sha256 = _normalize_hex(v)
    fname = (rom_buf.get("filename") or rom_buf.get("file name") or "").strip() or None
    roms.append(
        NoIntroRomChecksum(
            size=int(size_digits),
            crc32=crc,
            md5=md5,
            sha1=sha1,
            sha256=sha256,
            filename=fname,
        )
    )


def parse_nointro_submission(text: str) -> NoIntroSubmission:
    """Parse dumping.guide common dump info structure (plain key: value)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    fields: dict[str, str] = {}
    roms: list[NoIntroRomChecksum] = []
    in_rom = False
    rom_buf: dict[str, str] = {}

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if _ROM_SECTION.match(line):
            _flush_rom(rom_buf, roms)
            rom_buf = {}
            in_rom = True
            continue
        m = _FIELD.match(line)
        if not m:
            continue
        key = _norm_key(m.group(1))
        value = m.group(2).strip()
        if in_rom:
            rom_field_keys = _SIZE_KEYS | _CRC_KEYS | _MD5_KEYS | _SHA1_KEYS | _SHA256_KEYS | {
                "filename",
                "file name",
            }
            if key not in rom_field_keys:
                _flush_rom(rom_buf, roms)
                rom_buf = {}
                in_rom = False
                fields[key] = value
                continue
            if key in _SIZE_KEYS and rom_buf and any(k in rom_buf for k in _SIZE_KEYS):
                _flush_rom(rom_buf, roms)
                rom_buf = {k: v for k, v in rom_buf.items() if k not in _SIZE_KEYS}
            rom_buf[key] = value
        else:
            fields[key] = value

    _flush_rom(rom_buf, roms)
    return NoIntroSubmission(fields=fields, roms=roms)


def nointro_submission_valid(
    submission: NoIntroSubmission,
    *,
    require_fields: list[str] | None = None,
    require_checksums: list[str] | None = None,
) -> bool:
    if not (submission.game_name or "").strip():
        return False
    if not submission.roms:
        return False
    req_fields = require_fields or []
    for name in req_fields:
        key = _norm_key(name)
        if key in _GAME_NAME_KEYS:
            if not submission.game_name:
                return False
            continue
        if key in {_norm_key(k) for k in _SYSTEM_FIELD_KEYS} or key == "system":
            if not submission.dat_system:
                return False
            continue
        if not (submission.fields.get(key) or "").strip():
            return False
    req_cs = [c.strip().casefold() for c in (require_checksums or ["sha1", "md5"])]
    for rom in submission.roms:
        if "sha1" in req_cs and not rom.sha1:
            return False
        if "md5" in req_cs and not rom.md5:
            return False
        if "crc32" in req_cs and not rom.crc32:
            return False
        if "sha256" in req_cs and not rom.sha256:
            return False
        if rom.size <= 0:
            return False
    return True


def nointro_catalog_sha1(submission: NoIntroSubmission) -> str | None:
    if not submission.roms:
        return None
    return submission.roms[0].sha1
