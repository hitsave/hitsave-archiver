"""dump_platforms: Game template Platform / Media Format and the storage link."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dump_platforms as dp  # noqa: E402
from dump_catalog_omeka import _append_platform_media, _storage_uri_row  # noqa: E402

# Snapshot of archive.hitsave.org custom vocabularies 4 (Platform) and 5 (Media Format).
PLATFORM_TERMS = {
    'Amiga',
    'Apple Mac OS',
    'Apple Mac OS 8',
    'Apple Mac OS 9',
    'Apple Mac OS X',
    'Apple Mac OS X v10.0',
    'Apple Mac OS X v10.1',
    'Apple Mac OS X v10.10',
    'Apple Mac OS X v10.2',
    'Apple Mac OS X v10.3',
    'Apple Mac OS X v10.4',
    'Apple Mac OS X v10.5',
    'Apple Mac OS X v10.6',
    'Apple Mac OS X v10.7',
    'Apple Mac OS X v10.8',
    'Apple Mac OS X v10.9',
    'Apple System 6',
    'Apple System 7',
    'Atari 2600',
    'Atari 2600 (NTSC)',
    'Atari 2600 (PAL)',
    'Atari 2600 (SECAM)',
    'Emerson Arcadia 2001',
    'Linux',
    'MS-DOS',
    'MS-DOS 3.x',
    'MS-DOS 4.x',
    'MS-DOS 5.x',
    'MS-DOS 6.x',
    'Microsoft Windows',
    'Microsoft Windows 3.1',
    'Microsoft Windows 7',
    'Microsoft Windows 8',
    'Microsoft Windows 8.1',
    'Microsoft Windows 95',
    'Microsoft Windows 98',
    'Microsoft Windows ME',
    'Microsoft Windows Vista',
    'Microsoft Windows XP',
    'Microsoft Xbox',
    'Microsoft Xbox (NTSC-J)',
    'Microsoft Xbox (NTSC-U/C)',
    'Microsoft Xbox (PAL)',
    'Microsoft Xbox 360',
    'Microsoft Xbox 360 (NTSC-J)',
    'Microsoft Xbox 360 (NTSC-U/C)',
    'Microsoft Xbox 360 (PAL)',
    'Microsoft Xbox One',
    'New Nintendo 3DS',
    'New Nintendo 3DS (NTSC-J)',
    'New Nintendo 3DS (NTSC-U/C)',
    'New Nintendo 3DS (PAL)',
    'Nintendo 3DS',
    'Nintendo 3DS (NTSC-J)',
    'Nintendo 3DS (NTSC-U/C)',
    'Nintendo 3DS (PAL)',
    'Nintendo 64',
    'Nintendo 64 (NTSC-J)',
    'Nintendo 64 (NTSC-U/C)',
    'Nintendo 64 (PAL)',
    'Nintendo DS',
    'Nintendo DSi',
    'Nintendo DSi (NTSC-J)',
    'Nintendo DSi (NTSC-U/C)',
    'Nintendo DSi (PAL)',
    'Nintendo Entertainment System',
    'Nintendo Entertainment System (NTSC)',
    'Nintendo Entertainment System (PAL-A)',
    'Nintendo Entertainment System (PAL-B)',
    'Nintendo Family Computer',
    'Nintendo Game Boy',
    'Nintendo Game Boy Advance',
    'Nintendo Game Boy Color',
    'Nintendo GameCube',
    'Nintendo GameCube (NTSC-J)',
    'Nintendo GameCube (NTSC-K)',
    'Nintendo GameCube (NTSC-U/C)',
    'Nintendo GameCube (PAL)',
    'Nintendo Super Famicom',
    'Nintendo Switch',
    'Nintendo Virtual Boy',
    'Nintendo Wii',
    'Nintendo Wii (NTSC-J)',
    'Nintendo Wii (NTSC-K)',
    'Nintendo Wii (NTSC-U/C)',
    'Nintendo Wii (PAL)',
    'Nintendo Wii U',
    'Nintendo Wii U (NTSC-J)',
    'Nintendo Wii U (NTSC-U/C)',
    'Nintendo Wii U (PAL)',
    'Sega CD',
    'Sega Dreamcast',
    'Sega Game Gear',
    'Sega Genesis',
    'Sega Genesis (NTSC)',
    'Sega Genesis (PAL)',
    'Sega Genesis 32X',
    'Sega Mega Drive',
    'Sega Mega Drive 32X',
    'Sega Mega-CD',
    'Sega Mega-CD (NTSC-J)',
    'Sega Mega-CD (PAL)',
    'Sega Saturn',
    'Sega Saturn (NTSC)',
    'Sega Saturn (NTSC-J)',
    'Sega Saturn (PAL)',
    'Sega Super 32X',
    'Sony PlayStation',
    'Sony PlayStation (NTSC-J)',
    'Sony PlayStation (NTSC-U/C)',
    'Sony PlayStation (PAL)',
    'Sony PlayStation 2',
    'Sony PlayStation 2 (NTSC-J)',
    'Sony PlayStation 2 (NTSC-U/C)',
    'Sony PlayStation 2 (PAL)',
    'Sony PlayStation 3',
    'Sony PlayStation 4',
    'Sony PlayStation 5',
    'Sony PlayStation Portable',
    'Sony PlayStation Vita',
    'Super Nintendo Entertainment System',
    'Super Nintendo Entertainment System (NTSC)',
    'Super Nintendo Entertainment System (PAL)',
}
MEDIA_TERMS = {
    '3 1/2 inch floppy disk',
    '5 1/4 inch floppy disk',
    'Atari 2600 cartridge',
    'Atari 2600 cartridge (NTSC)',
    'Atari 2600 cartridge (PAL)',
    'Atari 2600 cartridge (SECAM)',
    'Blu-ray disc',
    'CD-ROM',
    'DVD',
    'Emerson Arcadia 2001 cartridge',
    'Nintendo 3DS Game Card',
    'Nintendo 3DS Game Card (NTSC-J)',
    'Nintendo 3DS Game Card (NTSC-U/C)',
    'Nintendo 3DS Game Card (PAL)',
    'Nintendo 64 Game Pak',
    'Nintendo 64 Game Pak (NTSC-J)',
    'Nintendo 64 Game Pak (NTSC-U/C)',
    'Nintendo 64 Game Pak (PAL)',
    'Nintendo DS Game Card',
    'Nintendo DSi Game Card',
    'Nintendo DSi Game Card (NTSC-J)',
    'Nintendo DSi Game Card (NTSC-U/C)',
    'Nintendo DSi Game Card (PAL)',
    'Nintendo Entertainment System Game Pak',
    'Nintendo Entertainment System Game Pak (NTSC)',
    'Nintendo Entertainment System Game Pak (PAL-A)',
    'Nintendo Entertainment System Game Pak (PAL-B)',
    'Nintendo Family Computer Game Pak',
    'Nintendo Game Boy Advance Game Pak',
    'Nintendo Game Boy Color Game Pak',
    'Nintendo Game Boy Game Pak',
    'Nintendo GameCube Disc',
    'Nintendo GameCube Disc (NTSC-J)',
    'Nintendo GameCube Disc (NTSC-K)',
    'Nintendo GameCube Disc (NTSC-U/C)',
    'Nintendo GameCube Disc (PAL)',
    'Nintendo Super Famicom Game Pak',
    'Nintendo Switch Game Card',
    'Nintendo Virtual Boy Game Pak (North America)',
    'Nintendo Wii Disc',
    'Nintendo Wii Disc (NTSC-J)',
    'Nintendo Wii Disc (NTSC-K)',
    'Nintendo Wii Disc (NTSC-U/C)',
    'Nintendo Wii Disc (PAL)',
    'Nintendo Wii U Disc',
    'Nintendo Wii U Disc (NTSC-J)',
    'Nintendo Wii U Disc (NTSC-U/C)',
    'Nintendo Wii U Disc (PAL)',
    'Sega 32X cartridge',
    'Sega Game Gear cartridge',
    'Sega Saturn cartridge',
    'Sega Saturn cartridge (NTSC)',
    'Sega Saturn cartridge (NTSC-J)',
    'Sega Saturn cartridge (PAL)',
    'Sega Super 32X cartridge',
    'Super Nintendo Entertainment System Game Pak',
    'Super Nintendo Entertainment System Game Pak (NTSC)',
    'Super Nintendo Entertainment System Game Pak (PAL)',
    'Universal Media Disc',
}


def test_every_mapped_term_exists_in_the_vocabularies() -> None:
    for system, (platform, media) in dp._PLATFORM_MEDIA.items():
        assert platform is None or platform in PLATFORM_TERMS, (system, platform)
        assert media is None or media in MEDIA_TERMS, (system, media)
    for alias, canon in dp._ALIASES.items():
        assert canon in dp._PLATFORM_MEDIA, alias
    for term in (dp.CD, dp.DVD, dp.BD, dp.UMD):
        assert term in MEDIA_TERMS


@pytest.mark.parametrize("system,media_type,expected", [
    ("Nintendo Switch", None, ("Nintendo Switch", "Nintendo Switch Game Card")),
    ("Nintendo - Nintendo Switch", None, ("Nintendo Switch", "Nintendo Switch Game Card")),
    ("Sony PlayStation 4", "BD-ROM-50", ("Sony PlayStation 4", "Blu-ray disc")),
    ("Sony PlayStation 2", "CD-ROM", ("Sony PlayStation 2", "CD-ROM")),
    ("Sony PlayStation 2", "DVD-ROM-9", ("Sony PlayStation 2", "DVD")),
    ("Sony PlayStation", None, ("Sony PlayStation", "CD-ROM")),
    ("Nintendo GameCube", "DVD-ROM", ("Nintendo GameCube", "Nintendo GameCube Disc")),
    ("Sega Dreamcast", "GD-ROM", ("Sega Dreamcast", None)),
    ("Nintendo - Game Boy", None, ("Nintendo Game Boy", "Nintendo Game Boy Game Pak")),
    ("DVD-Video", "DVD-ROM-5", (None, None)),
    ("Audio CD", "CD-ROM", (None, None)),
])
def test_platform_media(system, media_type, expected) -> None:
    assert dp.platform_media(system, media_type) == expected


def test_storage_link() -> None:
    url, label = dp.storage_link(
        "s3://hitsave-dumps/dumps/No-Intro/Nintendo - Nintendo Switch/S/switch-aka-1/",
        "https://s3.us-east-2.wasabisys.com",
    )
    assert url == ("https://s3.us-east-2.wasabisys.com/hitsave-dumps/dumps/No-Intro/"
                   "Nintendo%20-%20Nintendo%20Switch/S/switch-aka-1/")
    assert label == ("https://s3.us-east-2.wasabisys.com/hitsave-dumps/dumps/No-Intro/"
                     "Nintendo - Nintendo Switch/S/switch-aka-1/")
    other = "https://archive.hitsave.org/staff/dumps/x/"
    assert dp.storage_link(other, "https://e") == (other, other)
    assert dp.storage_link(None, "https://e") is None


class _Api:
    def resolve_property_id(self, session, base, creds, term):
        return {"dcterms:relation": 13, "dcterms:format": 9, "bibo:uri": 99}[term]

    def uri_value(self, pid, value, *, is_public=None):
        return {"property_id": pid, "type": "uri", "@id": value, "is_public": is_public}


CFG = {"platform_custom_vocab_id": 4, "media_format_custom_vocab_id": 5}


def test_vocab_values_for_switch() -> None:
    values: dict = {}
    assert _append_platform_media(values, None, "b", {}, _Api(), CFG, "Nintendo Switch", None)
    assert values["dcterms:relation"] == [
        {"property_id": 13, "type": "customvocab:4", "@value": "Nintendo Switch", "is_public": True}]
    assert values["dcterms:format"] == [
        {"property_id": 9, "type": "customvocab:5", "@value": "Nintendo Switch Game Card", "is_public": True}]


def test_falls_back_to_literal_without_vocab_ids() -> None:
    seen: list = []
    values: dict = {}
    assert not _append_platform_media(values, None, "b", {}, _Api(), {}, "Nintendo Switch", None,
                                      fallback_literal=lambda term, v: seen.append((term, v)))
    assert values == {} and seen == [("dcterms:format", "Nintendo Switch")]


def test_unmapped_system_keeps_literals() -> None:
    values: dict = {}
    assert not _append_platform_media(values, None, "b", {}, _Api(), CFG, "DVD-Video", "DVD-ROM-5")
    assert values == {}


def test_storage_uri_row_labels_s3() -> None:
    row = _storage_uri_row(_Api(), 99, "s3://hitsave-dumps/a b/", {"endpoint": "https://s3.example"},
                           is_public=False)
    assert row["@id"] == "https://s3.example/hitsave-dumps/a%20b/"
    assert row["o:label"] == "https://s3.example/hitsave-dumps/a b/"
