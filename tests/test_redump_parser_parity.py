#!/usr/bin/env python3
"""Redump parser SSOT: archiver dump_redump matches gdrive-intake when sibling repo is present."""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GDRIVE_PARSER = ROOT.parent / "hitsave-gdrive-intake" / "src" / "intake" / "redump" / "submission_info.py"
ARCHIVER_PARSER = ROOT / "scripts" / "dump_redump.py"
FIXTURE = ROOT / "fixtures" / "redump-submissionInfo-fwd0307.txt"


def _normalize_parser_source(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith("#") or "sync-redump-parser" in line.lower():
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def test_archiver_parser_parses_fwd0307_fixture() -> None:
    from dump_redump import parse_submission_info  # noqa: E402

    sub = parse_submission_info(FIXTURE.read_text(encoding="utf-8"))
    assert sub.system == "DVD-Video"
    assert sub.category == "Coverdiscs"
    assert sub.foreign_title is not None


def test_gdrive_parser_matches_archiver_when_present() -> None:
    if not GDRIVE_PARSER.is_file():
        return
    arch = _normalize_parser_source(ARCHIVER_PARSER.read_text(encoding="utf-8"))
    gdrive = _normalize_parser_source(GDRIVE_PARSER.read_text(encoding="utf-8"))
    assert hashlib.sha256(arch.encode()).hexdigest() == hashlib.sha256(gdrive.encode()).hexdigest()
