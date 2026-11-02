"""Moving a finished Wasabi intake package to processed/{lane}/: path rules."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "mv", ROOT / "scripts" / "remote-intake" / "move_wasabi_package_to_processed.py")
mv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mv)


def test_lane_inbox_to_processed_lane() -> None:
    assert mv.plan("no-intro", "aka", "no-intro") == ("no-intro/aka", "processed/no-intro/aka")
    assert mv.plan("redump/", "FWD0812-2", "redump") == ("redump/FWD0812-2", "processed/redump/FWD0812-2")
    assert mv.plan("", "Press Kit", "others") == ("Press Kit", "processed/others/Press Kit")


@pytest.mark.parametrize("src,name,lane", [
    ("processed/redump", "x", "redump"),   # already processed
    ("redump", "a/b", "redump"),           # nested name
    ("redump", "..", "redump"),
    ("redump", "x", "famitsu"),            # unknown lane
])
def test_refuses_unsafe_moves(src: str, name: str, lane: str) -> None:
    with pytest.raises(ValueError):
        mv.plan(src, name, lane)
