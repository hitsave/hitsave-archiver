"""File format identification (PRONOM via Siegfried) for BagIt metadata sidecars."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from preservation_common import load_packaging_config


@dataclass(frozen=True)
class FormatHit:
    pronom_key: str | None
    format_name: str | None
    format_version: str | None


def load_format_identification_config() -> dict:
    pkg = load_packaging_config()
    defaults = {
        "enabled": False,
        "tool": "siegfried",
        "binary": "sf",
        "pronom_registry_name": "PRONOM",
    }
    override = pkg.get("format_identification") or {}
    if isinstance(override, dict):
        defaults.update(override)
    return defaults


def siegfried_version(binary: str) -> str:
    try:
        proc = subprocess.run(
            [binary, "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    line = (proc.stdout or proc.stderr or "").strip().splitlines()
    return line[0].strip() if line else "unknown"


def _best_match(matches: list[dict]) -> dict | None:
    for row in matches:
        if str(row.get("ns", "")).lower() == "pronom" and row.get("id"):
            return row
    return matches[0] if matches else None


def identify_directory(tree_root: Path, cfg: dict) -> dict[str, FormatHit]:
    """Map paths relative to *tree_root* (posix) to PRONOM/format designation."""
    if not cfg.get("enabled", False):
        return {}
    binary = str(cfg.get("binary") or "sf")
    if not tree_root.is_dir():
        return {}
    try:
        proc = subprocess.run(
            [binary, "-json", str(tree_root)],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Format identification failed: {exc}") from exc
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"Format identification failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[:500]}"
        )
    payload = json.loads(proc.stdout or "{}")
    out: dict[str, FormatHit] = {}
    for entry in payload.get("files") or []:
        filename = entry.get("filename")
        if not filename:
            continue
        path = Path(filename)
        try:
            rel = path.resolve().relative_to(tree_root.resolve()).as_posix()
        except ValueError:
            rel = path.name
        match = _best_match(entry.get("matches") or [])
        if not match:
            out[rel] = FormatHit(pronom_key=None, format_name=None, format_version=None)
            continue
        pronom = match.get("id") if str(match.get("ns", "")).lower() == "pronom" else None
        out[rel] = FormatHit(
            pronom_key=str(pronom) if pronom else None,
            format_name=(match.get("format") or match.get("mime") or None),
            format_version=match.get("version") or None,
        )
    return out
