#!/usr/bin/env python3
"""Parse Redump submissionInfo.txt / !submissionInfo.txt (MPF + redumper layouts)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_ALT_TAG = re.compile(r"\[T:ALT\]\s*(.+)", re.I)
_ALTF_TAG = re.compile(r"\[T:ALTF\]\s*(.+)", re.I)
_SECTION_HEADER = re.compile(r"^([^:\t].*):\s*$")
_ROOT_FIELD = re.compile(r"^([^:\t]+):\s*(.*)$")
_SECTION_FIELD = re.compile(r"^\t([^:]+):\s*(.*)$")
_ROM_TAG = re.compile(
    r'<rom name="([^"]+)" size="(\d+)" crc="([a-f0-9]+)" md5="([a-f0-9]+)" sha1="([a-f0-9]+)"\s*/>',
    re.I,
)


@dataclass(frozen=True)
class DiscChecksums:
    size: int | None = None
    crc32: str | None = None
    md5: str | None = None
    sha1: str | None = None


@dataclass(frozen=True)
class RomChecksum:
    """Per-file checksum from Audio CD (and similar) <rom /> entries in submissionInfo."""

    name: str
    size: int
    crc32: str
    md5: str
    sha1: str


@dataclass
class RedumpSubmission:
    fields: dict[str, str] = field(default_factory=dict)
    sections: dict[str, dict[str, str]] = field(default_factory=dict)
    alternate_titles: list[str] = field(default_factory=list)
    foreign_alternate_titles: list[str] = field(default_factory=list)
    checksums: DiscChecksums = field(default_factory=DiscChecksums)
    roms: list[RomChecksum] = field(default_factory=list)

    @property
    def title(self) -> str | None:
        return self.fields.get("Title") or None

    @property
    def foreign_title(self) -> str | None:
        return self.fields.get("Foreign Title (Non-latin)") or None

    @property
    def system(self) -> str | None:
        return (self.fields.get("System") or "").strip() or None

    @property
    def category(self) -> str | None:
        return (self.fields.get("Category") or "").strip() or None


def is_audio_cd_submission(submission: RedumpSubmission) -> bool:
    return (submission.system or "").strip().casefold() == "audio cd"


def rom_track_submission_valid(submission: RedumpSubmission) -> bool:
    """Multi-file Redump discs: per-track <rom /> checksums instead of Size & Checksum."""
    if not submission.roms:
        return False
    if not (submission.system or "").strip():
        return False
    if not (submission.category or "").strip():
        return False
    return all(rom.md5 and rom.sha1 and rom.crc32 for rom in submission.roms)


def audio_cd_submission_valid(submission: RedumpSubmission) -> bool:
    """Audio CD is a rom-track layout; kept for callers that filter by system."""
    return is_audio_cd_submission(submission) and rom_track_submission_valid(submission)


def redump_catalog_sha1(submission: RedumpSubmission) -> str | None:
    """SHA-1 for redump.info /discs?q= lookup (disc block or first track ROM)."""
    if submission.checksums.sha1:
        return submission.checksums.sha1
    if rom_track_submission_valid(submission):
        return submission.roms[0].sha1
    return None


def normalize_redump_text(value: str) -> str:
    return unicodedata.normalize("NFC", (value or "").strip())


def _contains_japanese_script(text: str) -> bool:
    for ch in text:
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF:
            return True
    return False


def _title_has_non_ascii_latin(text: str) -> bool:
    for ch in text:
        if ord(ch) > 127:
            return True
    return False


def pick_omeka_title_from_submission(submission: RedumpSubmission) -> str:
    """
    Omeka display title from submissionInfo.

    When Foreign Title uses Japanese script and Title is macron-heavy romanization
    (common on Famitsu / Weekly Famitsu coverdiscs), prefer the foreign title.
    """
    title = normalize_redump_text(submission.title or "")
    foreign = normalize_redump_text(submission.foreign_title or "")
    if foreign and _contains_japanese_script(foreign):
        if not title or _title_has_non_ascii_latin(title):
            return foreign
    if title:
        return title
    if foreign:
        return foreign
    for alt in submission.alternate_titles:
        alt_norm = normalize_redump_text(alt)
        if alt_norm:
            return alt_norm
    return ""


def _normalize_hex(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower().replace(" ", "")
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    return cleaned or None


def _parse_rom_tags(text: str) -> list[RomChecksum]:
    out: list[RomChecksum] = []
    for name, size_raw, crc, md5, sha1 in _ROM_TAG.findall(text):
        md5_n = _normalize_hex(md5)
        sha1_n = _normalize_hex(sha1)
        crc_n = _normalize_hex(crc)
        if not md5_n or not sha1_n or not crc_n:
            continue
        out.append(
            RomChecksum(
                name=name.strip(),
                size=int(size_raw),
                crc32=crc_n,
                md5=md5_n,
                sha1=sha1_n,
            )
        )
    return out


def _parse_checksums(section: dict[str, str]) -> DiscChecksums:
    size_raw = (section.get("Size") or "").strip().replace(",", "")
    size = int(size_raw) if size_raw.isdigit() else None
    return DiscChecksums(
        size=size,
        crc32=_normalize_hex(section.get("CRC32")),
        md5=_normalize_hex(section.get("MD5")),
        sha1=_normalize_hex(section.get("SHA1")),
    )


def parse_submission_info(text: str) -> RedumpSubmission:
    """Parse Redump sidecar (section headers, tab-indented fields, checksum block)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: dict[str, dict[str, str]] = {}
    fields: dict[str, str] = {}
    current_section: str | None = None
    current_key: str | None = None
    buf: list[str] = []

    def flush_field() -> None:
        nonlocal current_key, buf
        if current_key is None:
            buf = []
            return
        value = "\n".join(buf).strip()
        if current_section:
            sections.setdefault(current_section, {})[current_key] = value
        if current_key not in fields or current_section in (
            "Common Disc Info",
            "Size & Checksum",
        ):
            fields[current_key] = value
        current_key = None
        buf = []

    for line in lines:
        sec_field = _SECTION_FIELD.match(line)
        if sec_field:
            key = sec_field.group(1).strip()
            if key.startswith("[") and current_key is not None:
                buf.append(line.strip())
                continue
            flush_field()
            current_key = key
            buf = [sec_field.group(2)]
            continue

        if line.startswith("\t") and current_key is not None:
            buf.append(line.strip())
            continue

        sec = _SECTION_HEADER.match(line)
        if sec and not line.startswith("\t"):
            flush_field()
            current_section = sec.group(1).strip()
            sections.setdefault(current_section, {})
            continue

        root = _ROOT_FIELD.match(line)
        if root and not line.startswith("\t"):
            flush_field()
            key = root.group(1).strip()
            rest = root.group(2)
            if rest.strip() == "" and key.endswith("Info"):
                current_section = key
                sections.setdefault(current_section, {})
                continue
            current_section = None
            current_key = key
            buf = [rest.strip()]
            continue

        if current_key is not None:
            buf.append(line)

    flush_field()

    checksums = _parse_checksums(sections.get("Size & Checksum") or {})
    roms = _parse_rom_tags(text)
    sub = RedumpSubmission(fields=fields, sections=sections, checksums=checksums, roms=roms)
    comments = fields.get("Comments") or ""
    for match in _ALT_TAG.finditer(text):
        title = match.group(1).strip()
        if title and title not in sub.alternate_titles:
            sub.alternate_titles.append(title)
    for match in _ALTF_TAG.finditer(text):
        title = match.group(1).strip()
        if title and title not in sub.foreign_alternate_titles:
            sub.foreign_alternate_titles.append(title)
    for match in _ALT_TAG.finditer(comments):
        sub.alternate_titles.append(match.group(1).strip())
    for match in _ALTF_TAG.finditer(comments):
        alt = match.group(1).strip()
        if alt and alt not in sub.foreign_alternate_titles:
            sub.foreign_alternate_titles.append(alt)
    return sub


# Used by hitsave-gdrive-intake verify (keep in sync via scripts/sync-redump-parser-to-gdrive.sh).
DISC_IMAGE_SUFFIXES = (".iso", ".bin", ".img", ".chd", ".gdi")
