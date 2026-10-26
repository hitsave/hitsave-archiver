#!/usr/bin/env python3
"""Tests for inbox → ledger slug matching helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inbox_rows_from_poll_json import wasabi_package_rows  # noqa: E402
from ledger_lookup_slugs import build_ledger_lookup_slugs  # noqa: E402


def test_gamewave_vol3_lookup_keys_include_disc_slug() -> None:
    poll_path = Path(__file__).resolve().parents[2].joinpath(".generated/wasabi-redump-poll.json")
    if not poll_path.is_file():
        return
    poll = json.loads(poll_path.read_text())
    row = next(
        r
        for r in wasabi_package_rows(poll)
        if r["ready_slug"] == "redump-gamewave-dvd-2000-nov-11-30-vol-3"
    )
    keys = row["ledger_lookup_slugs"]
    assert "redump-g-wave12-00" in keys
    assert "gamewave-dvd-2000-nov-11-30-vol-3" in keys


def test_build_keys_from_folder_only() -> None:
    keys = build_ledger_lookup_slugs(
        ready_slug="redump-gamewave-dvd-vol-12",
        package_folder_name="GameWave DVD VOL.12",
    )
    assert "gamewave-dvd-vol-12" in keys


if __name__ == "__main__":
    test_gamewave_vol3_lookup_keys_include_disc_slug()
    test_build_keys_from_folder_only()
    print("ok")
