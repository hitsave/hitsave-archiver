#!/usr/bin/env python3
"""Safe zip intake helpers for portable preservation submissions."""
from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

_UNIX_IFMT = 0o170000
_UNIX_IFREG = 0o100000
_UNIX_IFDIR = 0o040000
_UNIX_IFLNK = 0o120000

_EXTRACT_CHUNK = 1024 * 1024


class SubmissionError(Exception):
    """Invalid or unsafe submission archive."""


@dataclass(frozen=True)
class SubmissionManifest:
    game_key: str | None
    omeka_item_title: str | None
    material_type: str | None
    submitter: str | None
    notes: str | None


@dataclass(frozen=True)
class SubmissionLimits:
    max_zip_bytes: int
    max_uncompressed_bytes: int
    max_extracted_bytes: int
    max_archive_members: int
    max_path_length: int
    max_member_bytes: int
    max_manifest_bytes: int


@dataclass(frozen=True)
class SubmissionSecurity:
    reject_nested_archives: bool
    nested_archive_extensions: frozenset[str]
    allowed_extensions: frozenset[str] | None


def slug_game_key(base: str, *, prefix: str = "submission") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", base.strip()).strip("-").lower()
    if not slug:
        slug = "material"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    key = f"{prefix}-{stamp}-{slug}"
    return key[:120]


def limits_from_dict(lim: dict) -> SubmissionLimits:
    return SubmissionLimits(
        max_zip_bytes=int(lim.get("max_zip_bytes", 5368709120)),
        max_uncompressed_bytes=int(lim.get("max_uncompressed_bytes", 10737418240)),
        max_extracted_bytes=int(lim.get("max_extracted_bytes", lim.get("max_uncompressed_bytes", 10737418240))),
        max_archive_members=int(lim.get("max_archive_members", 50000)),
        max_path_length=int(lim.get("max_path_length", 240)),
        max_member_bytes=int(lim.get("max_member_bytes", 536870912)),
        max_manifest_bytes=int(lim.get("max_manifest_bytes", 65536)),
    )


def security_from_dict(sec: dict) -> SubmissionSecurity:
    nested = sec.get("nested_archive_extensions") or []
    nested_set = frozenset(
        e.lower() if e.startswith(".") else f".{e.lower()}" for e in nested if str(e).strip()
    )
    allowed_raw = sec.get("allowed_extensions")
    allowed: frozenset[str] | None
    if allowed_raw is None or allowed_raw == []:
        allowed = None
    else:
        allowed = frozenset(
            e.lower() if str(e).startswith(".") else f".{str(e).lower()}"
            for e in allowed_raw
        )
    return SubmissionSecurity(
        reject_nested_archives=bool(sec.get("reject_nested_archives", False)),
        nested_archive_extensions=nested_set,
        allowed_extensions=allowed,
    )


def _safe_member_name(name: str, max_path_length: int) -> str:
    if not name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise SubmissionError(f"Unsafe zip path: {name!r}")
    parts = []
    for part in Path(name).parts:
        if part in ("", ".", ".."):
            raise SubmissionError(f"Unsafe zip path segment in {name!r}")
        parts.append(part)
    normalized = str(Path(*parts))
    if len(normalized) > max_path_length:
        raise SubmissionError(f"Zip path too long: {name!r}")
    return normalized


def _check_zip_member_type(info: zipfile.ZipInfo) -> None:
    if info.create_system == 3:
        mode = (info.external_attr >> 16) & _UNIX_IFMT
        if mode == _UNIX_IFLNK:
            raise SubmissionError(f"Symlink zip entries not allowed: {info.filename!r}")
        if mode not in (0, _UNIX_IFREG, _UNIX_IFDIR):
            raise SubmissionError(f"Special file type in zip: {info.filename!r}")


def _check_nested_archive_member(name: str, security: SubmissionSecurity) -> None:
    if not security.reject_nested_archives:
        return
    lower = name.lower()
    for ext in security.nested_archive_extensions:
        if lower.endswith(ext):
            raise SubmissionError(f"Nested archive not allowed in submission zip: {name!r}")


def inspect_zip(
    zip_path: Path,
    limits: SubmissionLimits,
    security: SubmissionSecurity,
) -> tuple[int, int]:
    """Return (member_count, declared_uncompressed_total) after safety checks."""
    if not zip_path.is_file():
        raise SubmissionError(f"Not a file: {zip_path}")
    size = zip_path.stat().st_size
    if size > limits.max_zip_bytes:
        raise SubmissionError(f"Zip exceeds max_zip_bytes ({size} > {limits.max_zip_bytes})")

    members = 0
    total_uncompressed = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            members += 1
            if members > limits.max_archive_members:
                raise SubmissionError("Too many files in zip")
            _safe_member_name(info.filename, limits.max_path_length)
            _check_zip_member_type(info)
            if not info.is_dir():
                _check_nested_archive_member(info.filename, security)
                if int(info.file_size) > limits.max_member_bytes:
                    raise SubmissionError(
                        f"Zip member exceeds max_member_bytes: {info.filename!r}"
                    )
            if info.is_dir():
                continue
            total_uncompressed += int(info.file_size)
            if total_uncompressed > limits.max_uncompressed_bytes:
                raise SubmissionError("Uncompressed size exceeds max_uncompressed_bytes")
    return members, total_uncompressed


def extract_zip_safely(
    zip_path: Path,
    dest_dir: Path,
    limits: SubmissionLimits,
    security: SubmissionSecurity,
) -> int:
    """Extract zip; return total bytes written. Enforces streamed per-member caps."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    inspect_zip(zip_path, limits, security)
    dest_resolved = dest_dir.resolve()
    total_written = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            rel = _safe_member_name(info.filename, limits.max_path_length)
            _check_zip_member_type(info)
            target = (dest_dir / rel).resolve()
            if not str(target).startswith(str(dest_resolved)):
                raise SubmissionError(f"Zip slip blocked: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            _check_nested_archive_member(info.filename, security)
            target.parent.mkdir(parents=True, exist_ok=True)
            member_written = 0
            with zf.open(info, "r") as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(_EXTRACT_CHUNK)
                    if not chunk:
                        break
                    member_written += len(chunk)
                    total_written += len(chunk)
                    if member_written > limits.max_member_bytes:
                        raise SubmissionError(
                            f"Extracted member exceeds max_member_bytes: {info.filename!r}"
                        )
                    if total_written > limits.max_extracted_bytes:
                        raise SubmissionError("Extracted total exceeds max_extracted_bytes")
                    out.write(chunk)

    reject_symlinks_on_disk(dest_dir)
    return total_written


def reject_symlinks_on_disk(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        for name in dirnames + filenames:
            path = base / name
            if path.is_symlink():
                raise SubmissionError(f"Symlink not allowed on disk: {path}")


def validate_allowlist(root: Path, allowed: frozenset[str] | None) -> None:
    if allowed is None:
        return
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SubmissionError(f"Symlink not allowed: {path}")
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if not ext:
            raise SubmissionError(f"Extensionless files not allowed: {path}")
        if ext not in allowed:
            raise SubmissionError(f"Extension not allowed: {path} ({ext})")


def resolve_source_folder(extract_root: Path) -> Path:
    """Use single top-level directory when the zip wrapped one folder."""
    entries = [p for p in extract_root.iterdir() if not p.name.startswith(".")]
    dirs = [p for p in entries if p.is_dir() and not p.is_symlink()]
    files = [p for p in entries if p.is_file()]
    if len(dirs) == 1 and not files:
        return dirs[0]
    return extract_root


def load_manifest(
    extract_root: Path,
    filenames: list[str],
    *,
    max_manifest_bytes: int,
) -> SubmissionManifest:
    empty = SubmissionManifest(None, None, None, None, None)
    if yaml is None:
        return empty
    for name in filenames:
        path = extract_root / name
        if not path.is_file() or path.is_symlink():
            continue
        size = path.stat().st_size
        if size > max_manifest_bytes:
            raise SubmissionError(
                f"Manifest {name} exceeds max_manifest_bytes ({size} > {max_manifest_bytes})"
            )
        raw = path.read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > max_manifest_bytes:
            raise SubmissionError(f"Manifest {name} exceeds max_manifest_bytes")
        data = yaml.safe_load(raw) or {}
        if not isinstance(data, dict):
            raise SubmissionError(f"Manifest must be a mapping: {name}")
        return SubmissionManifest(
            game_key=str(data["game_key"]).strip() if data.get("game_key") else None,
            omeka_item_title=str(data["omeka_item_title"]).strip()
            if data.get("omeka_item_title")
            else None,
            material_type=str(data.get("material_type") or "").strip() or None,
            submitter=str(data.get("submitter") or "").strip() or None,
            notes=str(data.get("notes") or "").strip() or None,
        )
    return empty


def build_game_config(
    *,
    source_game_folder: Path,
    game_key: str,
    omeka_item_title: str,
    paths: dict,
    batch_key: str,
    video_access: dict | None = None,
) -> dict:
    """Submission layout under output/{dip,aip,.staging}/submissions/{batch_key}/ (not batch/)."""
    output_root = Path(paths["output_root"])
    dip_sub = paths.get("dip_subdir", "dip")
    aip_sub = paths.get("aip_subdir", "aip")
    dip_dir = output_root / dip_sub / "submissions" / batch_key
    aip_dir = output_root / aip_sub / "submissions" / batch_key
    staging_root = output_root / ".staging" / "submissions" / batch_key
    cfg: dict = {
        "source_game_folder": str(source_game_folder),
        "output_tar": str(dip_dir / f"{game_key}.tar"),
        "staging_dir": str(staging_root / game_key),
        "aip_bag_dir": str(aip_dir / game_key),
        "game_key": game_key,
        "omeka_item_title": omeka_item_title,
        "submission": {"batch_key": batch_key},
    }
    if video_access is not None:
        cfg["video_access"] = video_access
    return cfg
