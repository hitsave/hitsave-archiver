"""Unit tests for E-ARK AIP PREMIS helpers."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path("/app/scripts") if Path("/app/scripts").is_dir() else ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from format_identification import _best_match  # noqa: E402
from preservation_common import (  # noqa: E402
    package_objid,
    premis3_document,
    premis_digiprov_md_ref,
    premis_file_object_fragment,
)


def test_package_objid_adds_urn_prefix() -> None:
    assert package_objid("123e4567-e89b-12d3-a456-426655440000") == (
        "urn:uuid:123e4567-e89b-12d3-a456-426655440000"
    )
    assert package_objid("urn:uuid:abc") == "urn:uuid:abc"


def test_premis_file_object_includes_pronom() -> None:
    xml = premis_file_object_fragment(
        object_uuid="obj-1",
        filepath="data/screenshot.png",
        size=100,
        sha256="abc",
        pronom_key="fmt/11",
        format_name="PNG",
        format_version="1.0",
    )
    assert "fmt/11" in xml
    assert "PNG" in xml
    assert "messageDigest" in xml


def test_premis_digiprov_md_ref_version(tmp_path: Path) -> None:
    p = tmp_path / "PREMIS3.xml"
    p.write_text(premis3_document(body="  <agent></agent>"), encoding="utf-8")
    ref = premis_digiprov_md_ref(
        href="metadata/preservation/PREMIS3.xml",
        premis_path=p,
        created="2026-01-01T00:00:00Z",
    )
    assert 'MDTYPEVERSION="3.0"' in ref
    assert 'MIMETYPE="text/xml"' in ref


def test_siegfried_best_match_prefers_pronom() -> None:
    row = _best_match(
        [
            {"ns": "mime", "id": "image/png", "format": "PNG"},
            {"ns": "pronom", "id": "fmt/11", "format": "PNG"},
        ]
    )
    assert row is not None
    assert row["id"] == "fmt/11"
