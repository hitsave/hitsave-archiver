"""Damaged payload files of one package, read from its ledger inventory.

Shared by the ledger (item page, review queue, ?flag=damaged) and the public "Damaged files"
page, so both list the same files in the same words. Results come from
build-bagit-package.py or check-integrity-wasabi.py (inventory_json files[].integrity).
"""
from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

PUBLIC_PAGE_URL = "https://archive.hitsave.org/damaged-files"

# SQL condition: the row's inventory records at least one damaged file.
DAMAGED_SQL = (
    "jsonb_path_exists(inventory_json::jsonb, '$.files[*].integrity ? (@.status == \"damaged\")')"
)

CUT_OFF = ("truncat", "startxref", "trailer", "moov atom not found", "unexpected end", "eof", "end of data",
           "end of file", "read error on strip", "read error on tile")
NOT_THIS_TYPE = ("not a readable", "unknown or corrupt header", "can't open as archive", "not a pdf",
                 "invalid data found when processing input", "bad magic number")


def plain_problem(detail: str | None) -> str:
    text = (detail or "").lower()
    if any(m in text for m in CUT_OFF):
        return "Cut off: the end of the file is missing"
    if any(m in text for m in NOT_THIS_TYPE):
        return "Not a readable file of this type"
    return "Damaged data inside the file"


def file_type(entry: dict) -> str:
    name = (entry.get("format") or {}).get("name")
    if name:
        return str(name)
    ext = PurePosixPath(str(entry.get("path") or "")).suffix.lstrip(".").upper()
    return ext or "Unknown"


def damaged_files(inventory: Any) -> list[dict]:
    """[{path, name, folder, size_bytes, type, check, problem, detail}], sorted by folder then name.

    *inventory* is the ledger's inventory_json (a dict, its JSON text, or None). The folder
    leaves out the bag's leading data/.
    """
    if isinstance(inventory, str):
        try:
            inventory = json.loads(inventory)
        except ValueError:
            return []
    if not isinstance(inventory, dict):
        return []
    out = []
    for entry in inventory.get("files") or []:
        integrity = entry.get("integrity") or {}
        if integrity.get("status") != "damaged":
            continue
        rel = PurePosixPath(str(entry.get("path") or ""))
        parts = rel.parts[1:] if rel.parts and rel.parts[0] == "data" else rel.parts
        if not parts:
            continue
        out.append({
            "path": str(rel),
            "name": parts[-1],
            "folder": "/".join(parts[:-1]),
            "size_bytes": int(entry.get("size_bytes") or 0),
            "type": file_type(entry),
            "check": integrity.get("check") or "",
            "problem": plain_problem(integrity.get("detail")),
            "detail": integrity.get("detail") or "",
        })
    out.sort(key=lambda f: (f["folder"].lower(), f["name"].lower()))
    return out
