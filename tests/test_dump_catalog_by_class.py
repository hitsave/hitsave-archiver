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
