from dump_catalog_omeka import _redump_languages, literal_formats


def test_redump_languages_placeholder_is_omitted() -> None:
    fields = {"Languages": " ADD LANGUAGES HERE (ONLY IF YOU TESTED) "}
    assert _redump_languages(fields) is None


def test_redump_languages_real_value_kept() -> None:
    fields = {"Languages": "Japanese"}
    assert _redump_languages(fields) == "Japanese"


def test_literal_format_is_the_system_only() -> None:
    """DVD-Video coverdiscs show "DVD-Video", not the disc's Media Type (DVD-ROM-5 -> "DVD-5")."""
    assert literal_formats({"System": "DVD-Video", "Media Type": "DVD-ROM-5"}) == ["DVD-Video"]
    assert literal_formats({"System": "Audio CD", "Media Type": "CD-ROM"}) == ["Audio CD"]
    assert literal_formats({"Media Type": "DVD-ROM-9"}) == []
