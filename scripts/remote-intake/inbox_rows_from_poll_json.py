"""Parse intake poll JSON into normalized inbox rows (sweeps / diagnostics)."""
from __future__ import annotations

import re
from typing import Any

from ledger_lookup_slugs import build_ledger_lookup_slugs

DISC_SUFFIXES = (".iso", ".bin", ".img", ".chd", ".gdi")


def _has_disc_image(pkg: dict[str, Any]) -> bool:
    for f in pkg.get("files") or []:
        n = str(f.get("name") or "").lower()
        if any(n.endswith(s) for s in DISC_SUFFIXES):
            return True
    return False


def gdrive_package_rows(data: dict[str, Any]) -> list[dict[str, str | None]]:
    prefix = "redump"
    out: list[dict[str, str | None]] = []
    for pkg in data.get("packages") or []:
        name = str(pkg.get("name") or "").strip()
        fid = str(pkg.get("id") or "").strip()
        if not name or name == "processed":
            continue
        if not _has_disc_image(pkg):
            continue
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
        if not slug:
            continue
        ready = f"{prefix}-{slug}"
        out.append(
            {
                "ready_slug": ready,
                "package_folder_name": name,
                "source_prefix": "gdrive",
                "external_ref": fid or None,
                "ledger_lookup_slugs": build_ledger_lookup_slugs(
                    ready_slug=ready,
                    package_folder_name=name,
                    files=pkg.get("files"),
                ),
            }
        )
    return out


def wasabi_package_rows(data: dict[str, Any]) -> list[dict[str, str | None]]:
    out: list[dict[str, str | None]] = []
    for pkg in data.get("packages") or []:
        slug = str(pkg.get("ready_slug") or "").strip()
        if not slug:
            continue
        name = str(pkg.get("package_folder_name") or "").strip() or None
        src = str(pkg.get("source_prefix") or "").strip() or None
        ext = str(pkg.get("s3_prefix") or "").strip() or None
        out.append(
            {
                "ready_slug": slug,
                "package_folder_name": name,
                "source_prefix": src,
                "external_ref": ext,
                "ledger_lookup_slugs": build_ledger_lookup_slugs(
                    ready_slug=slug,
                    package_folder_name=name,
                    files=pkg.get("files"),
                ),
            }
        )
    return out


def rows_for_lane(lane: str, data: dict[str, Any]) -> list[dict[str, str | None]]:
    if lane == "gdrive":
        return gdrive_package_rows(data)
    if lane == "wasabi":
        return wasabi_package_rows(data)
    raise ValueError(f"unknown lane {lane!r}")
