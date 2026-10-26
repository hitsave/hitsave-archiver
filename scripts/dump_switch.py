#!/usr/bin/env python3
"""Parse Nintendo Switch No-Intro-style Submission.xml (datafile) sidecars."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


def _normalize_hex(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower().replace(" ", "")
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    return cleaned or None


@dataclass(frozen=True)
class SwitchFileEntry:
    size: int
    extension: str
    fmt: str | None = None
    item: str | None = None
    crc32: str | None = None
    md5: str | None = None
    sha1: str | None = None
    sha256: str | None = None


@dataclass
class SwitchSubmission:
    game_name: str
    region: str | None = None
    gameid1: str | None = None
    gameid2: str | None = None
    categories: str | None = None
    dumper: str | None = None
    project: str | None = None
    tool: str | None = None
    files: list[SwitchFileEntry] = field(default_factory=list)

    def primary_cart_image(self) -> SwitchFileEntry | None:
        """Default-format XCI (trusted dump), not FullXCI duplicate row."""
        for entry in self.files:
            if entry.extension.casefold() != "xci":
                continue
            if (entry.fmt or "").casefold() == "default":
                return entry
        for entry in self.files:
            if entry.extension.casefold() == "xci":
                return entry
        return None


def find_switch_submission_path(sip_dir: Path) -> Path | None:
    if not sip_dir.is_dir():
        return None
    for path in sorted(sip_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name.casefold()
        if name.endswith(" submission.xml") or name.endswith("-submission.xml"):
            return path
    return None


# Immediate child dirs under a game title folder that are not version SIPs.
_SWITCH_GAME_FOLDER_SKIP = frozenset({"scans", "personal"})


def resolve_switch_submission_path(sip_root: Path) -> Path | None:
    """Find * Submission.xml at SIP root or under one version subfolder (game title layout)."""
    found = find_switch_submission_path(sip_root)
    if found is not None:
        return found
    if not sip_root.is_dir():
        return None
    for child in sorted(sip_root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.casefold() in _SWITCH_GAME_FOLDER_SKIP:
            continue
        found = find_switch_submission_path(child)
        if found is not None:
            return found
    return None


def switch_version_label(source_dir: Path, submission_path: Path) -> str:
    if submission_path.parent.resolve() == source_dir.resolve():
        return source_dir.name
    return submission_path.parent.name


def switch_game_title(source_dir: Path, submission: SwitchSubmission) -> str:
    """Contributor folder name when SIP is a game title tree; XML name when SIP is a version folder."""
    if find_switch_submission_path(source_dir) is not None:
        return submission.game_name
    return source_dir.name.strip() or submission.game_name


def parse_switch_submission_xml(text: str) -> SwitchSubmission:
    root = ET.fromstring(text)
    game_el = root.find("game")
    if game_el is None:
        raise ValueError("switch submission missing <game>")
    archive = game_el.find("archive")
    source = game_el.find("source")
    if source is None:
        raise ValueError("switch submission missing <source>")

    game_name = (game_el.get("name") or "").strip()
    if not game_name:
        raise ValueError("switch submission missing game name")

    region = archive.get("region") if archive is not None else None
    categories = archive.get("categories") if archive is not None else None
    gameid1 = archive.get("gameid1") if archive is not None else None
    gameid2 = archive.get("gameid2") if archive is not None else None

    details = source.find("details")
    dumper = project = tool = None
    if details is not None:
        dumper = (details.get("dumper") or "").strip() or None
        project = (details.get("project") or "").strip() or None
        tool = (details.get("tool") or "").strip() or None

    files: list[SwitchFileEntry] = []
    for file_el in source.findall("file"):
        size_raw = (file_el.get("size") or "").strip()
        if not size_raw.isdigit():
            continue
        files.append(
            SwitchFileEntry(
                size=int(size_raw),
                extension=(file_el.get("extension") or "").strip(),
                fmt=(file_el.get("format") or "").strip() or None,
                item=(file_el.get("item") or "").strip() or None,
                crc32=_normalize_hex(file_el.get("crc32")),
                md5=_normalize_hex(file_el.get("md5")),
                sha1=_normalize_hex(file_el.get("sha1")),
                sha256=_normalize_hex(file_el.get("sha256")),
            )
        )

    return SwitchSubmission(
        game_name=game_name,
        region=(region or "").strip() or None,
        gameid1=(gameid1 or "").strip() or None,
        gameid2=(gameid2 or "").strip() or None,
        categories=(categories or "").strip() or None,
        dumper=dumper,
        project=project,
        tool=tool,
        files=files,
    )


def switch_submission_valid(submission: SwitchSubmission) -> bool:
    if not submission.game_name.strip():
        return False
    primary = submission.primary_cart_image()
    if primary is None:
        return False
    if primary.size <= 0:
        return False
    if not primary.sha256 and not primary.sha1:
        return False
    return True


def switch_catalog_sha256(submission: SwitchSubmission) -> str | None:
    primary = submission.primary_cart_image()
    return primary.sha256 if primary else None
