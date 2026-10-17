from dump_catalog_omeka import _omeka_format_from_redump, _redump_languages


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
