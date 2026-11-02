"""Status-web catalog correction → Windmill job args."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "status-web"))

from catalog_correct import build_windmill_args, description_for_windmill  # noqa: E402


def _base_values(**overrides: str) -> dict[str, str]:
    values = {
        "omeka_ref": "123",
        "title": "",
        "moby_url": "",
        "description": "",
        "developer": "",
        "publisher": "",
        "clear_moby_url": "",
        "clear_developer": "",
        "clear_publisher": "",
        "reset_ledger_moby": "true",
        "execute": "true",
        "former_title": "",
        "correction_summary": "",
    }
    values.update(overrides)
    return values


def test_clear_moby_omits_empty_description():
    values = _base_values(clear_moby_url="true")
    assert description_for_windmill(values) is None
    args = build_windmill_args(values)
    assert args["clear_moby_url"] is True
    assert "description" not in args


def test_clear_moby_keeps_explicit_description_override():
    values = _base_values(
        clear_moby_url="true",
        description="Custom press note only.",
    )
    args = build_windmill_args(values)
    assert args["description"] == "Custom press note only."
