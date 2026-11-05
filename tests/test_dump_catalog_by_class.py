"""dump_catalog.by_dump_class: Switch dumps are games, not Famitsu Wave coverdiscs."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dump_catalog_omeka import dump_catalog_config  # noqa: E402

BASE = {
    "item_set_title": "Famitsu Wave", "item_set_id": 511, "resource_template_label": "Moving Image",
    "storage_uri_is_public": False, "is_public": True, "storage_uri_base": "https://x/staff/dumps",
    "site_url": "https://redump.info", "disc_url": "{site}/disc/{disc_id}", "lookup_timeout_seconds": 30,
    "user_agent": "t",
}


def _cfg(by_class=None):
    d = dict(BASE)
    if by_class is not None:
        d["by_dump_class"] = by_class
    return {"dump_catalog": d}


SWITCH = {"switch": {"item_set_title": "Video Games", "item_set_id": 572, "resource_template_label": "Game"}}


def test_defaults_without_class_or_override() -> None:
    for cfg, cls in ((_cfg(), "switch"), (_cfg(SWITCH), None), (_cfg(SWITCH), "redump")):
        c = dump_catalog_config(cfg, cls)
        assert (c["item_set_title"], c["item_set_id"], c["resource_template_label"]) == ("Famitsu Wave", 511, "Moving Image")


def test_switch_override() -> None:
    c = dump_catalog_config(_cfg(SWITCH), "switch")
    assert (c["item_set_title"], c["item_set_id"], c["resource_template_label"]) == ("Video Games", 572, "Game")
    assert c["is_public"] is True and c["storage_uri_base"] == "https://x/staff/dumps"  # untouched


def test_title_only_override_drops_default_id() -> None:
    c = dump_catalog_config(_cfg({"no-intro": {"item_set_title": "Video Games"}}), "no-intro")
    assert c["item_set_title"] == "Video Games" and c["item_set_id"] is None


def test_unknown_override_key_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        dump_catalog_config(_cfg({"switch": {"is_public": False}}), "switch")


GAMES_DEFAULT = {**BASE, "item_set_title": "Video Games", "item_set_id": 572, "resource_template_label": "Game",
                 "by_dump_system": {"DVD-Video": {"item_set_title": "Famitsu Wave", "item_set_id": 511,
                                                  "resource_template_label": "Moving Image"}}}


@pytest.mark.parametrize("cls,system,expected", [
    ("redump", "DVD-Video", ("Famitsu Wave", 511, "Moving Image")),     # coverdiscs
    ("redump", "dvd-video", ("Famitsu Wave", 511, "Moving Image")),     # case-insensitive
    ("redump", "Sony PlayStation 4", ("Video Games", 572, "Game")),     # a PS4 redump is a game
    ("switch", "Nintendo Switch", ("Video Games", 572, "Game")),
    ("no-intro", "Nintendo - Game Boy", ("Video Games", 572, "Game")),
])
def test_system_overrides(cls, system, expected) -> None:
    c = dump_catalog_config({"dump_catalog": GAMES_DEFAULT}, cls, system)
    assert (c["item_set_title"], c["item_set_id"], c["resource_template_label"]) == expected


def test_system_beats_class() -> None:
    d = {**GAMES_DEFAULT, "by_dump_class": {"redump": {"item_set_title": "Software", "item_set_id": 1107}}}
    assert dump_catalog_config({"dump_catalog": d}, "redump", "DVD-Video")["item_set_id"] == 511
    assert dump_catalog_config({"dump_catalog": d}, "redump", "Sony PlayStation 4")["item_set_id"] == 1107


def test_vocab_ids_pass_through() -> None:
    d = dict(GAMES_DEFAULT, platform_custom_vocab_id=4, media_format_custom_vocab_id="5")
    c = dump_catalog_config({"dump_catalog": d}, "switch")
    assert (c["platform_custom_vocab_id"], c["media_format_custom_vocab_id"]) == (4, 5)
    c = dump_catalog_config({"dump_catalog": GAMES_DEFAULT}, "switch")
    assert c["platform_custom_vocab_id"] is None and c["media_format_custom_vocab_id"] is None


def test_system_category_is_most_specific() -> None:
    d = {**GAMES_DEFAULT, "by_system_category": {
        "Sony PlayStation/Coverdiscs": {"item_set_title": "Famitsu Wave", "item_set_id": 511}}}
    cfg = {"dump_catalog": d}
    c = dump_catalog_config(cfg, "redump", "Sony PlayStation", "Coverdiscs")
    assert (c["item_set_id"], c["resource_template_label"]) == (511, "Game")   # set only; still Game
    c = dump_catalog_config(cfg, "redump", "sony playstation", "coverdiscs")   # case-insensitive
    assert c["item_set_id"] == 511
    assert dump_catalog_config(cfg, "redump", "Sony PlayStation", "Games")["item_set_id"] == 572
    assert dump_catalog_config(cfg, "redump", "Sony PlayStation")["item_set_id"] == 572
