"""Derive game_ingest lookup keys from remote inbox package metadata."""
from __future__ import annotations

import re
from typing import Any

DISC_SUFFIXES = (".iso", ".bin", ".img", ".chd", ".gdi")
_TOP_LEVEL_DISC = re.compile(
    r"^(?:[A-Z]{1,2}_WAVE[\d_]+|G_WAVE[\d_]+|F_WAVE[\d_]+|SLPM?[_-]?\d+|SLBM[_-]?\d+)$",
    re.IGNORECASE,
)


def slug_game_key(prefix: str, folder_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", folder_name.strip()).strip("-").lower()
    if not slug:
        slug = "disc"
    return f"{prefix}-{slug}"


def _strip_redump(slug: str) -> str | None:
    if slug.startswith("redump-"):
        return slug[7:]
    return None


def _disc_folder_names_from_files(files: list[Any]) -> list[str]:
    names: list[str] = []
    for entry in files:
        path = entry if isinstance(entry, str) else str(entry.get("name") or entry.get("Key") or "")
        path = path.strip().lstrip("/")
        if not path:
            continue
        parts = path.split("/")
        if len(parts) >= 2:
            top = parts[0]
            leaf = parts[-1].lower()
            if any(leaf.endswith(s) for s in DISC_SUFFIXES) and _TOP_LEVEL_DISC.match(top):
                names.append(top)
        elif len(parts) == 1 and any(path.lower().endswith(s) for s in DISC_SUFFIXES):
            stem = path.rsplit(".", 1)[0]
            if _TOP_LEVEL_DISC.match(stem):
                names.append(stem)
    return names


def build_ledger_lookup_slugs(
    *,
    ready_slug: str,
    package_folder_name: str | None = None,
    files: list[Any] | None = None,
    prefix: str = "redump",
) -> list[str]:
    """Ordered unique keys to match game_ingest.game_key or source_path."""
    keys: list[str] = []
    seen: set[str] = set()

    def add(*candidates: str) -> None:
        for c in candidates:
            c = c.strip()
            if not c or c in seen:
                continue
            seen.add(c)
            keys.append(c)

    add(ready_slug)
    bare = _strip_redump(ready_slug)
    if bare:
        add(bare)
    if package_folder_name:
        add(slug_game_key(prefix, package_folder_name))
        folder_slug = re.sub(r"[^a-zA-Z0-9]+", "-", package_folder_name.strip()).strip("-").lower()
        if folder_slug:
            add(folder_slug)
            add(f"{prefix}-{folder_slug}")
    for disc in _disc_folder_names_from_files(list(files or [])):
        add(slug_game_key(prefix, disc))
    return keys
