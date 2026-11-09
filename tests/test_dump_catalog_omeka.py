import pytest

from dump_catalog_omeka import _omeka_format_from_redump, _redump_field, _redump_languages


def test_redump_languages_placeholder_is_omitted() -> None:
    fields = {"Languages": " ADD LANGUAGES HERE (ONLY IF YOU TESTED) "}
    assert _redump_languages(fields) is None


def test_redump_languages_real_value_kept() -> None:
    fields = {"Languages": "Japanese"}
    assert _redump_languages(fields) == "Japanese"


def test_omeka_format_dvd_rom_disc_sizes() -> None:
    assert _omeka_format_from_redump("Media Type", "DVD-ROM-5") == "DVD-5"
    assert _omeka_format_from_redump("Media Type", "dvd-rom-9") == "DVD-9"
    assert _omeka_format_from_redump("Media Type", "DVD-ROM") == "DVD-ROM"


def test_omeka_format_system_unchanged() -> None:
    assert _omeka_format_from_redump("System", "DVD-Video") == "DVD-Video"


@pytest.mark.parametrize("raw", [
    "SILENCE! (CHANGE THIS)",  # dumping tool's Languages default (Square Enix E3 2005 Media CD)
    "(OPTIONAL)",
    "(REQUIRED, IF EXISTS)",
    "ADD LANGUAGES HERE (ONLY IF YOU TESTED)",
    "  ",
])
def test_template_placeholders_never_reach_the_catalog(raw: str) -> None:
    assert _redump_field({"Languages": raw}, "Languages") is None
    assert _redump_languages({"Languages": raw}) is None


def test_placeholder_check_applies_to_every_field() -> None:
    fields = {"Region": "SILENCE! (CHANGE THIS)", "Disc Serial": "ADD LANGUAGES HERE (ONLY IF YOU TESTED)"}
    assert _redump_field(fields, "Region") is None
    assert _redump_field(fields, "Disc Serial") is None


def test_real_values_kept() -> None:
    assert _redump_field({"Languages": "English, French"}, "Languages") == "English, French"
    assert _redump_field({"Region": "USA"}, "Region") == "USA"
