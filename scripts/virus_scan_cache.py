"""Virus scan report saved by `ingest-folder.py --scan-only` and reused by the ingest that follows.

The sweeps scan a staged package before anything opens its files (DVD menu preview), then ingest
it. Scanning twice would double the slowest step for multi-GB disc images, so the scan-only run
saves its report under <output_root>/virus-scan/<game_key>.json together with every file's size
and mtime. The ingest reuses it only when the package is the same folder with exactly the same
files, sizes and mtimes, and the report is recent; otherwise it scans again.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from clamav_full_scan import FileScan
from premis_virus_check import VirusScanReport

CACHE_VERSION = 1
CACHE_SUBDIR = "virus-scan"
MAX_AGE_SECONDS = 24 * 3600


def report_path(output_root: Path, game_key: str) -> Path:
    return Path(output_root) / CACHE_SUBDIR / f"{game_key}.json"


def file_stamps(root: Path) -> dict[str, list[int]]:
    """{relative path: [size, mtime_ns]} for every file scan_sip_tree scans (dotfiles included)."""
    stamps: dict[str, list[int]] = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            st = path.stat()
            stamps[path.relative_to(root).as_posix()] = [st.st_size, st.st_mtime_ns]
    return stamps


def save_report(
    path: Path, report: VirusScanReport, root: Path, *, now: Callable[[], float] = time.time
) -> None:
    data = {
        "version": CACHE_VERSION,
        "source": str(Path(root).resolve()),
        "saved_at": now(),
        "stamps": file_stamps(root),
        "report": asdict(report),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _report_from_dict(raw: dict) -> VirusScanReport:
    raw = dict(raw)
    raw["file_scans"] = [FileScan(**fs) for fs in raw.get("file_scans") or []]
    return VirusScanReport(**raw)


def load_matching_report(
    path: Path,
    root: Path,
    *,
    max_age_seconds: float = MAX_AGE_SECONDS,
    now: Callable[[], float] = time.time,
) -> tuple[VirusScanReport | None, str]:
    """(report, "") when the saved scan still covers this package, else (None, why it doesn't)."""
    if not path.is_file():
        return None, "no saved scan"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != CACHE_VERSION:
            return None, "saved scan has an old format"
        if data.get("source") != str(Path(root).resolve()):
            return None, f"saved scan was of another folder ({data.get('source')})"
        age = now() - float(data["saved_at"])
        if age < 0 or age > max_age_seconds:
            return None, f"saved scan is too old ({int(age // 3600)} h)"
        if data.get("stamps") != file_stamps(root):
            return None, "files changed since the saved scan"
        return _report_from_dict(data["report"]), ""
    except (OSError, ValueError, KeyError, TypeError) as e:
        return None, f"saved scan unreadable ({e})"
