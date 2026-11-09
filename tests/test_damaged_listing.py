"""Damaged files of one package, read from its ledger inventory (shared by the ledger and the public page)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import damaged_listing as dl  # noqa: E402
import ledger_health_report as lhr  # noqa: E402

INVENTORY = {"files": [
    {"path": "data/Kit/Scans/z.jpg", "size_bytes": 10,
     "integrity": {"status": "damaged", "check": "pillow", "detail": "image file is truncated (2 bytes not processed)"}},
    {"path": "data/Kit/ok.jpg", "size_bytes": 5, "integrity": {"status": "ok"}},
    {"path": "data/Kit/pw.zip", "size_bytes": 5, "integrity": {"status": "unchecked", "detail": "password"}},
    {"path": "data/Kit/no-check.iso", "size_bytes": 5},
    {"path": "data/Kit/A.pdf", "size_bytes": 2_000, "format": {"name": "Acrobat PDF 1.4"},
     "integrity": {"status": "damaged", "check": "qpdf", "detail": "can't find PDF header"}},
]}


def test_damaged_files_lists_only_damaged_sorted_by_folder_then_name() -> None:
    files = dl.damaged_files(INVENTORY)
    assert [(f["folder"], f["name"]) for f in files] == [("Kit", "A.pdf"), ("Kit/Scans", "z.jpg")]
    pdf = files[0]
    assert pdf["path"] == "data/Kit/A.pdf"
    assert pdf["type"] == "Acrobat PDF 1.4" and pdf["size_bytes"] == 2_000
    assert pdf["check"] == "qpdf" and pdf["detail"] == "can't find PDF header"
    assert files[1]["type"] == "JPG"
    assert files[1]["problem"] == "Cut off: the end of the file is missing"


def test_damaged_files_copes_with_missing_or_odd_inventories() -> None:
    assert dl.damaged_files(None) == []
    assert dl.damaged_files({}) == []
    assert dl.damaged_files({"files": None}) == []
    assert dl.damaged_files('{"files": [{"path": "data/a.png", "integrity": {"status": "damaged"}}]}')[0]["name"] == "a.png"


def test_damaged_flag_uses_the_shared_condition() -> None:
    cond, label = lhr.FLAG_CONDITIONS["damaged"]
    assert cond == dl.DAMAGED_SQL
    assert label == "has damaged files"
    assert dl.DAMAGED_SQL in lhr.DAMAGED_COUNT_SQL
