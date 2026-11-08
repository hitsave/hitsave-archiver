"""Labelled press kits are catalogued in the press material item set, whatever the disc."""
from __future__ import annotations

from dump_catalog_omeka import dump_catalog_config, is_labelled_press_kit

BASE = {
    "item_set_title": "Video Games",
    "item_set_id": 572,
    "resource_template_label": "Game",
    "storage_uri_is_public": False,
    "is_public": True,
    "storage_uri_base": "https://example.invalid",
    "site_url": "https://redump.info",
    "disc_url": "{site}/disc/{disc_id}",
    "lookup_timeout_seconds": 30,
    "user_agent": "t",
    "by_system_category": {"DVD-Video/Bonus Discs": {"item_set_title": "Video Game Bonus Discs", "item_set_id": 5483}},
    "press_kit": {"item_set_title": "Press and Marketing Materials", "item_set_id": 1459},
}


def _manifest(**fields) -> dict:
    folder = fields.pop("folder_name", None)
    return {"dump_category": fields.get("Category"), "folder_name": folder, "redump": {"parsed": {"fields": fields}}}


def test_press_kit_label_sources() -> None:
    assert is_labelled_press_kit(_manifest(Category="Multimedia", **{"Edition/Release": "Press Kit"}))
    assert is_labelled_press_kit(_manifest(Category="Multimedia", Comments="Press disc released at E3 2002."))
    assert is_labelled_press_kit(_manifest(Category="Bonus Discs", folder_name="09 - Titus (Press Kit)"))
    assert is_labelled_press_kit(_manifest(Category="Multimedia", Editions="Press Kit"))  # MPF sidecar key
    assert not is_labelled_press_kit(_manifest(Category="Multimedia", **{"Edition/Release": "Original"}))
    assert not is_labelled_press_kit(_manifest(Category="Games", **{"Edition/Release": "Press Kit"}))  # software


def test_press_kit_set_beats_system_and_category() -> None:
    cfg = {"dump_catalog": BASE}
    assert dump_catalog_config(cfg, "redump", "IBM PC compatible", "Multimedia", press_kit=True)["item_set_id"] == 1459
    assert dump_catalog_config(cfg, "redump", "DVD-Video", "Bonus Discs", press_kit=True)["item_set_id"] == 1459
    assert dump_catalog_config(cfg, "redump", "DVD-Video", "Bonus Discs")["item_set_id"] == 5483
    assert dump_catalog_config(cfg, "redump", "IBM PC compatible", "Multimedia")["item_set_id"] == 572
    no_rule = {"dump_catalog": {k: v for k, v in BASE.items() if k != "press_kit"}}
    assert dump_catalog_config(no_rule, "redump", "IBM PC compatible", "Multimedia", press_kit=True)["item_set_id"] == 572
