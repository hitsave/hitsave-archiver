"""File format identification (PRONOM via Siegfried) for inventory.json.

identify_tree() runs ``sf -json`` over a directory and never raises: a missing binary, a bad
exit or unreadable output is returned as a FormatRun with status "failed", so format
identification can't stop a package from being preserved. The inventory then says so.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from preservation_common import load_packaging_config

DEFAULT_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class FormatHit:
    """One file's identification. puid is None when PRONOM has no match."""

    puid: str | None
    name: str | None = None
    version: str | None = None
    mime: str | None = None
    basis: str | None = None
    warning: str | None = None
    error: str | None = None

    def record(self) -> dict:
        out: dict = {"puid": self.puid}
        for key in ("name", "version", "mime", "basis", "warning", "error"):
            value = getattr(self, key)
            if value:
                out[key] = value
        return out


@dataclass
class FormatRun:
    status: str  # "ok", "failed" or "disabled"
    hits: dict[str, FormatHit] = field(default_factory=dict)
    tool: str = "siegfried"
    signature: str | None = None
    identified_at: str = ""
    error: str | None = None

    def record(self) -> dict:
        out: dict = {"status": self.status, "tool": self.tool}
        if self.signature:
            out["signature"] = self.signature
        out["identified_at"] = self.identified_at
        if self.error:
            out["error"] = self.error
        return out


def load_format_identification_config() -> dict:
    pkg = load_packaging_config()
    defaults = {
        "enabled": False,
        "tool": "siegfried",
        "binary": "sf",
        "pronom_registry_name": "PRONOM",
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
    }
    override = pkg.get("format_identification") or {}
    if isinstance(override, dict):
        defaults.update(override)
    return defaults


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _pronom_match(matches: list[dict]) -> dict | None:
    for row in matches:
        if str(row.get("ns", "")).lower() == "pronom":
            return row
    return matches[0] if matches else None


def _hit(entry: dict) -> FormatHit:
    error = (entry.get("errors") or "").strip() or None
    match = _pronom_match(entry.get("matches") or [])
    if not match:
        return FormatHit(puid=None, error=error)
    puid = str(match.get("id") or "").strip()
    if not puid or puid.upper() == "UNKNOWN":
        return FormatHit(puid=None, warning=match.get("warning") or None, error=error)
    return FormatHit(
        puid=puid,
        name=match.get("format") or None,
        version=match.get("version") or None,
        mime=match.get("mime") or None,
        basis=match.get("basis") or None,
        warning=match.get("warning") or None,
        error=error,
    )


def _signature(payload: dict) -> str | None:
    sig = payload.get("signature")
    if not sig:
        return None
    extra = [payload.get("created") or ""]
    extra += [i.get("details") or "" for i in payload.get("identifiers") or []]
    extra = [e for e in extra if e]
    return f"{sig} ({'; '.join(extra)})" if extra else str(sig)


def identify_tree(tree_root: Path, cfg: dict | None = None) -> FormatRun:
    """Identify every file under *tree_root*; hits are keyed by posix path relative to it."""
    cfg = load_format_identification_config() if cfg is None else cfg
    started = _now()
    if not cfg.get("enabled", False):
        return FormatRun(status="disabled", identified_at=started)
    binary = str(cfg.get("binary") or "sf")
    timeout = int(cfg.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    try:
        # -coe: keep going past a file sf can't read; that file gets an "errors" entry.
        proc = subprocess.run(
            [binary, "-json", "-coe", str(tree_root)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return FormatRun(status="failed", identified_at=started, error=f"{binary}: {exc}")
    if proc.returncode not in (0, 1):
        detail = (proc.stderr or proc.stdout or "").strip()[:500]
        return FormatRun(status="failed", identified_at=started,
                         error=f"{binary} exit {proc.returncode}: {detail}")
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return FormatRun(status="failed", identified_at=started, error=f"unreadable sf output: {exc}")
    root = tree_root.resolve()
    hits: dict[str, FormatHit] = {}
    for entry in payload.get("files") or []:
        filename = entry.get("filename")
        if not filename:
            continue
        try:
            rel = Path(filename).resolve().relative_to(root).as_posix()
        except ValueError:
            rel = Path(filename).name
        hits[rel] = _hit(entry)
    version = payload.get("siegfried")
    return FormatRun(
        status="ok",
        hits=hits,
        tool=f"siegfried {version}" if version else "siegfried",
        signature=_signature(payload),
        identified_at=started,
    )
