"""Switch catalog title must not be the slug-named ready/ folder."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dump_catalog_omeka import switch_catalog_title  # noqa: E402


def test_game_tree_uses_package_title(tmp_path: Path) -> None:
    sip = tmp_path / "switch-bad-end-theater"
    (sip / "1.0.3").mkdir(parents=True)
    (sip / "1.0.3" / "Bad End Theater - hitsaveorg - 2026-05-01 Submission.xml").write_text("<datafile/>")
    sw = SimpleNamespace(game_name="Bad End Theater")
    assert switch_catalog_title(sip, sw, "Bad End Theater") == "Bad End Theater"


def test_root_submission_uses_xml_name(tmp_path: Path) -> None:
    sip = tmp_path / "switch-1-0-3"
    sip.mkdir()
    (sip / "Aka - 1.0.6 - hitsaveorg - 2026-05-04 Submission.xml").write_text("<datafile/>")
    assert switch_catalog_title(sip, SimpleNamespace(game_name="Aka"), "switch-1-0-3") == "Aka"
