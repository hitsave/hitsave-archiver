#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ingest_kind import (  # noqa: E402
    moby_enrich_enabled,
    moby_omeka_profile_for_kind,
    resolve_ingest_kind,
    storage_profile_for_kind,
    wasabi_root_for_kind,
)


def test_missing_ingest_kind_raises() -> None:
    try:
        resolve_ingest_kind({"source_subpath": "A/Foo"})
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "must set ingest_kind" in str(exc)


def test_dump_kind_uses_game_catalog_moby_profile() -> None:
    assert resolve_ingest_kind({"ingest_kind": "dump"}) == "dump"
    assert moby_omeka_profile_for_kind("dump") == "game_catalog"
    assert moby_enrich_enabled({"ingest_kind": "dump"}) is True
    assert storage_profile_for_kind("dump") == "bagit_unpacked"


def test_legacy_game_and_disc_dump_aliases_normalize_to_dump() -> None:
    assert resolve_ingest_kind({"ingest_kind": "game_dump"}) == "dump"
    assert resolve_ingest_kind({"ingest_kind": "disc_dump"}) == "dump"
    assert moby_enrich_enabled({"ingest_kind": "disc_dump"}) is True


def test_ingest_kind_aliases_hyphens() -> None:
    assert resolve_ingest_kind({"ingest_kind": "game-dump"}) == "dump"


def test_analog_kind() -> None:
    assert resolve_ingest_kind({"ingest_kind": "analog"}) == "analog"
    assert wasabi_root_for_kind("analog") == "analog"
    assert moby_enrich_enabled({"ingest_kind": "analog"}) is False
    assert storage_profile_for_kind("analog") == "bagit_unpacked"


def test_press_material_unpacked_on_wasabi() -> None:
    assert storage_profile_for_kind("press_material") == "bagit_unpacked"
