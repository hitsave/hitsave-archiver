#!/usr/bin/env python3
"""Parse Redump submissionInfo.txt / !submissionInfo.txt (MPF + redumper layouts)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_ALT_TAG = re.compile(r"\[T:ALT\]\s*(.+)", re.I)
_ALTF_TAG = re.compile(r"\[T:ALTF\]\s*(.+)", re.I)
_SECTION_HEADER = re.compile(r"^([^:\t].*):\s*$")
_ROOT_FIELD = re.compile(r"^([^:\t]+):\s*(.*)$")
_SECTION_FIELD = re.compile(r"^\t([^:]+):\s*(.*)$")


@dataclass(frozen=True)
class DiscChecksums:
    size: int | None = None
    crc32: str | None = None
    md5: str | None = None
    sha1: str | None = None


@dataclass
class RedumpSubmission:
    fields: dict[str, str] = field(default_factory=dict)
    sections: dict[str, dict[str, str]] = field(default_factory=dict)
    alternate_titles: list[str] = field(default_factory=list)
    foreign_alternate_titles: list[str] = field(default_factory=list)
    checksums: DiscChecksums = field(default_factory=DiscChecksums)

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


def _normalize_hex(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower().replace(" ", "")
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    return cleaned or None


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
    sub = RedumpSubmission(fields=fields, sections=sections, checksums=checksums)
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
