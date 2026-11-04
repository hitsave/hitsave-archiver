"""Where an intake package came from, as one comparable string.

Intake records the source of every package it stages in ready/<slug>/hitsave-submission.yaml:
a Google Drive folder id, or a Wasabi ``<source_prefix>/<package_folder>``. That file lives on
the intake host's scratch disk, so ingest also copies the source into the ledger
(``game_ingest.intake_source``) in one canonical form:

    drive:<folder id>
    wasabi:<prefix/package folder>      (no leading/trailing slash)
    local:<absolute path>               (ingested by hand from a folder on the intake host; the
                                         sweep never produces this, so a swept package sharing
                                         the slug is always a mismatch)

The slug-collision guard (remote-intake/ready_source.py) compares against the ledger value first
and only falls back to the handoff file when the ledger has none.
"""
from __future__ import annotations

from pathlib import Path

import yaml

MANIFEST_NAME = "hitsave-submission.yaml"
# intake writes the source into the lane's block: redump, no_intro (No-Intro/Switch) or others.
SOURCE_BLOCKS = ("redump", "no_intro", "others")


def norm_prefix(value: str | None) -> str:
    return str(value or "").strip().strip("/")


def source_from_manifest(data: dict | None) -> dict:
    """{'drive_folder_id': ...} / {'wasabi_prefix': ...} / {} (no source in the manifest)."""
    data = data or {}
    for key in SOURCE_BLOCKS:
        block = data.get(key)
        if not isinstance(block, dict):
            continue
        fid = str(block.get("drive_folder_id") or "").strip()
        if fid:
            return {"drive_folder_id": fid}
        prefix = norm_prefix(block.get("wasabi_source_prefix"))
        if prefix:
            return {"wasabi_prefix": prefix}
    return {}


def recorded_source(ready_dir: Path) -> dict | None:
    """Source recorded in ready_dir's handoff manifest; None when there is no manifest."""
    manifest = Path(ready_dir) / MANIFEST_NAME
    if not manifest.is_file():
        return None
    return source_from_manifest(yaml.safe_load(manifest.read_text(encoding="utf-8")))


def to_ref(rec: dict | None) -> str | None:
    """Canonical string for a source dict (None when it records no source)."""
    if not rec:
        return None
    if rec.get("drive_folder_id"):
        return f"drive:{str(rec['drive_folder_id']).strip()}"
    if rec.get("wasabi_prefix"):
        return f"wasabi:{norm_prefix(rec['wasabi_prefix'])}"
    if rec.get("local_path"):
        return f"local:{str(rec['local_path']).strip().rstrip('/')}"
    return None


def parse_ref(ref: str | None) -> dict | None:
    """Inverse of to_ref; None for empty or unrecognised values."""
    ref = str(ref or "").strip()
    kind, sep, value = ref.partition(":")
    if not sep or not value.strip():
        return None
    if kind == "drive":
        return {"drive_folder_id": value.strip()}
    if kind == "wasabi" and norm_prefix(value):
        return {"wasabi_prefix": norm_prefix(value)}
    if kind == "local" and value.strip().startswith("/") and value.strip().rstrip("/"):
        return {"local_path": value.strip().rstrip("/")}
    return None


def current_ref(*, drive_folder_id: str | None = None, wasabi_prefix: str | None = None) -> str:
    """Canonical string for the package being processed right now."""
    if drive_folder_id:
        return f"drive:{str(drive_folder_id).strip()}"
    if wasabi_prefix and norm_prefix(wasabi_prefix):
        return f"wasabi:{norm_prefix(wasabi_prefix)}"
    raise ValueError("pass drive_folder_id or wasabi_prefix")
