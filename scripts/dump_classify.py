#!/usr/bin/env python3
"""Classify dump SIPs (Redump sidecar vs Other) and compute Wasabi prefix segments."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dump_nointro import NoIntroSubmission, nointro_submission_valid, parse_nointro_submission
from dump_redump import RedumpSubmission, parse_submission_info, rom_track_submission_valid
from ingest_kind import ingest_kind_spec, load_ingest_kinds_config

_UNSAFE_KEY = re.compile(r"[/\\]+")


@dataclass(frozen=True)
class DumpClassification:
    dump_class: str  # redump | no-intro | other
    dump_system: str | None
    dump_category: str | None
    package_storage_prefix: str  # no bucket, no trailing slash
    redump_submission: RedumpSubmission | None = None


def storage_path_segment(value: str) -> str:
    """One path segment from Redump System/Category (preserve readable Redump labels)."""
    segment = value.strip()
    if not segment:
        return "Unknown"
    return _UNSAFE_KEY.sub("-", segment)


def _dump_layout_cfg() -> dict[str, Any]:
    kinds = load_ingest_kinds_config().get("ingest_kinds") or {}
    dump = kinds.get("dump") or {}
    return dict(dump.get("dump_layout") or {})


def find_submission_info_path(sip_dir: Path, names: list[str]) -> Path | None:
    lowered = {n.casefold() for n in names}
    for path in sip_dir.iterdir():
        if path.is_file() and path.name.casefold() in lowered:
            return path
    return None


def _checksum_ok(submission: RedumpSubmission, required: list[str]) -> bool:
    for name in required:
        key = name.strip().casefold()
        if key == "sha1" and not submission.checksums.sha1:
            return False
        if key == "md5" and not submission.checksums.md5:
            return False
        if key == "crc32" and not submission.checksums.crc32:
            return False
    return True


def redump_submission_valid(
    submission: RedumpSubmission,
    *,
    require_sections: list[str],
    require_fields: list[str],
    require_checksums: list[str],
) -> bool:
    if rom_track_submission_valid(submission):
        require_sections = [s for s in require_sections if s != "Size & Checksum"]
        require_checksums = []

    for section in require_sections:
        if section == "Common Disc Info":
            if "Common Disc Info" not in submission.sections and "System" not in submission.fields:
                return False
        elif section == "Size & Checksum":
            if "Size & Checksum" not in submission.sections and not submission.checksums.sha1:
                return False
        elif section not in submission.sections:
            return False
    for field in require_fields:
        if not (submission.fields.get(field) or "").strip():
            return False
    return _checksum_ok(submission, require_checksums)


def classify_dump_sip(
    sip_dir: Path,
    *,
    package_id: str,
    sort_letter: str = "X",
) -> DumpClassification:
    """Detect Redump or No-Intro sidecar, else classify as Other."""
    layout = _dump_layout_cfg()
    redump_cfg = layout.get("redump") or {}
    nointro_cfg = layout.get("no_intro") or {}
    other_cfg = layout.get("other") or {}
    wasabi_root = str(ingest_kind_spec("dump").get("wasabi_root") or "dumps").strip("/")
    letter = (sort_letter or "X").strip("/") or "X"

    redump_names = list(redump_cfg.get("submission_info_names") or [])
    sub_path = find_submission_info_path(sip_dir, redump_names) if redump_names else None
    if sub_path is not None:
        text = sub_path.read_text(encoding="utf-8", errors="replace")
        submission = parse_submission_info(text)
        if redump_submission_valid(
            submission,
            require_sections=list(redump_cfg.get("require_sections") or []),
            require_fields=list(redump_cfg.get("require_fields") or []),
            require_checksums=list(redump_cfg.get("require_checksums") or []),
        ):
            system = storage_path_segment(submission.system or "Unknown")
            category = storage_path_segment(submission.category or "Unknown")
            pattern = str(
                redump_cfg.get("path_pattern") or "{system}/{category}/{sort_letter}/{package_id}"
            )
            prefix = pattern.format(
                system=system,
                category=category,
                sort_letter=letter,
                package_id=package_id,
            ).strip("/")
            return DumpClassification(
                dump_class="redump",
                dump_system=system,
                dump_category=category,
                package_storage_prefix=f"{wasabi_root}/{prefix}",
                redump_submission=submission,
            )

    ni_names = list(nointro_cfg.get("submission_info_names") or [])
    ni_path = find_submission_info_path(sip_dir, ni_names) if ni_names else None
    if ni_path is not None:
        ni_text = ni_path.read_text(encoding="utf-8", errors="replace")
        ni_sub: NoIntroSubmission = parse_nointro_submission(ni_text)
        if nointro_submission_valid(
            ni_sub,
            require_fields=list(nointro_cfg.get("require_fields") or []),
            require_checksums=list(nointro_cfg.get("require_checksums") or []),
        ):
            system = storage_path_segment(ni_sub.dat_system or "Unknown")
            pattern = str(
                nointro_cfg.get("path_pattern") or "No-Intro/{system}/{sort_letter}/{package_id}"
            )
            prefix = pattern.format(
                system=system,
                sort_letter=letter,
                package_id=package_id,
            ).strip("/")
            return DumpClassification(
                dump_class="no-intro",
                dump_system=system,
                dump_category="ROM",
                package_storage_prefix=f"{wasabi_root}/{prefix}",
                redump_submission=None,
            )

    letter = (sort_letter or "X").strip("/") or "X"
    pattern = str(other_cfg.get("path_pattern") or "Others/{sort_letter}/{package_id}")
    prefix = pattern.format(sort_letter=letter, package_id=package_id).strip("/")
    wasabi_root = str(ingest_kind_spec("dump").get("wasabi_root") or "dumps").strip("/")
    return DumpClassification(
        dump_class="other",
        dump_system=None,
        dump_category=None,
        package_storage_prefix=f"{wasabi_root}/{prefix}",
        redump_submission=None,
    )


def package_storage_s3_uri(bucket: str, package_storage_prefix: str) -> str:
    return f"s3://{bucket.strip()}/{package_storage_prefix.strip('/')}/"
