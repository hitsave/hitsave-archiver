"""Omeka Game template fields for dumps: Platform (dcterms:relation) and Media Format (dcterms:format).

Both are custom vocabularies on archive.hitsave.org ("Platform" and "Media Format"; ids configured
in omeka-catalog.yaml dump_catalog.platform_custom_vocab_id / media_format_custom_vocab_id). The
terms below must match those vocabularies exactly; None = no fitting term (the value is left out).

dump_system is the Redump "System" / No-Intro platform / "Nintendo Switch". DVD-Video and Audio CD
are deliberately absent: those coverdiscs keep their literal format values (Moving Image template).
"""
from __future__ import annotations

import re

# Disc media terms; a Redump "Media Type" can refine a disc system's default (PS2 on CD vs DVD).
CD, DVD, BD, UMD = "CD-ROM", "DVD", "Blu-ray disc", "Universal Media Disc"
_DISC_TERMS = {CD, DVD, BD, UMD}

# system name -> (Platform term, Media Format term)
_PLATFORM_MEDIA: dict[str, tuple[str | None, str | None]] = {
    # Nintendo
    "Nintendo Switch": ("Nintendo Switch", "Nintendo Switch Game Card"),
    "Nintendo GameCube": ("Nintendo GameCube", "Nintendo GameCube Disc"),
    "Nintendo Wii": ("Nintendo Wii", "Nintendo Wii Disc"),
    "Nintendo Wii U": ("Nintendo Wii U", "Nintendo Wii U Disc"),
    "Nintendo 64": ("Nintendo 64", "Nintendo 64 Game Pak"),
    "Nintendo Entertainment System": ("Nintendo Entertainment System", "Nintendo Entertainment System Game Pak"),
    "Nintendo Family Computer": ("Nintendo Family Computer", "Nintendo Family Computer Game Pak"),
    "Super Nintendo Entertainment System": ("Super Nintendo Entertainment System",
                                            "Super Nintendo Entertainment System Game Pak"),
    "Nintendo Super Famicom": ("Nintendo Super Famicom", "Nintendo Super Famicom Game Pak"),
    "Nintendo Game Boy": ("Nintendo Game Boy", "Nintendo Game Boy Game Pak"),
    "Nintendo Game Boy Color": ("Nintendo Game Boy Color", "Nintendo Game Boy Color Game Pak"),
    "Nintendo Game Boy Advance": ("Nintendo Game Boy Advance", "Nintendo Game Boy Advance Game Pak"),
    "Nintendo DS": ("Nintendo DS", "Nintendo DS Game Card"),
    "Nintendo DSi": ("Nintendo DSi", "Nintendo DSi Game Card"),
    "Nintendo 3DS": ("Nintendo 3DS", "Nintendo 3DS Game Card"),
    "New Nintendo 3DS": ("New Nintendo 3DS", "Nintendo 3DS Game Card"),
    "Nintendo Virtual Boy": ("Nintendo Virtual Boy", "Nintendo Virtual Boy Game Pak (North America)"),
    # Sega
    "Sega Genesis": ("Sega Genesis", None),
    "Sega Mega Drive": ("Sega Mega Drive", None),
    "Sega CD": ("Sega CD", CD),
    "Sega Mega CD": ("Sega Mega-CD", CD),
    "Sega 32X": ("Sega Genesis 32X", "Sega 32X cartridge"),
    "Sega Game Gear": ("Sega Game Gear", "Sega Game Gear cartridge"),
    "Sega Saturn": ("Sega Saturn", CD),
    "Sega Dreamcast": ("Sega Dreamcast", None),  # GD-ROM: no vocabulary term
    # Sony
    "Sony PlayStation": ("Sony PlayStation", CD),
    "Sony PlayStation 2": ("Sony PlayStation 2", DVD),
    "Sony PlayStation 3": ("Sony PlayStation 3", BD),
    "Sony PlayStation 4": ("Sony PlayStation 4", BD),
    "Sony PlayStation 5": ("Sony PlayStation 5", BD),
    "Sony PlayStation Portable": ("Sony PlayStation Portable", UMD),
    "Sony PlayStation Vita": ("Sony PlayStation Vita", None),
    # Microsoft
    "Microsoft Xbox": ("Microsoft Xbox", DVD),
    "Microsoft Xbox 360": ("Microsoft Xbox 360", DVD),
    "Microsoft Xbox One": ("Microsoft Xbox One", BD),
}

# Other spellings (No-Intro DAT names, Redump variants) -> canonical key above.
_ALIASES = {
    "Nintendo - Nintendo Switch": "Nintendo Switch",
    "Nintendo - GameCube": "Nintendo GameCube",
    "Nintendo - Wii": "Nintendo Wii",
    "Nintendo - Wii U": "Nintendo Wii U",
    "Nintendo - Nintendo 64": "Nintendo 64",
    "Nintendo - Nintendo Entertainment System": "Nintendo Entertainment System",
    "Nintendo - Family Computer": "Nintendo Family Computer",
    "Nintendo - Super Nintendo Entertainment System": "Super Nintendo Entertainment System",
    "Nintendo - Super Famicom": "Nintendo Super Famicom",
    "Nintendo - Game Boy": "Nintendo Game Boy",
    "Nintendo - Game Boy Color": "Nintendo Game Boy Color",
    "Nintendo - Game Boy Advance": "Nintendo Game Boy Advance",
    "Nintendo - Nintendo DS": "Nintendo DS",
    "Nintendo - Nintendo DSi": "Nintendo DSi",
    "Nintendo - Nintendo 3DS": "Nintendo 3DS",
    "Nintendo - New Nintendo 3DS": "New Nintendo 3DS",
    "Nintendo - Virtual Boy": "Nintendo Virtual Boy",
    "Sega - Mega Drive - Genesis": "Sega Genesis",
    "Sega Mega CD & Sega CD": "Sega CD",
    "Sega - 32X": "Sega 32X",
    "Sega - Game Gear": "Sega Game Gear",
    "Sony - PlayStation Portable": "Sony PlayStation Portable",
    "Sony - PlayStation Vita": "Sony PlayStation Vita",
}


def _key(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").casefold()).strip()


_TABLE = {_key(k): v for k, v in _PLATFORM_MEDIA.items()}
_TABLE.update({_key(alias): _PLATFORM_MEDIA[canon] for alias, canon in _ALIASES.items()})


def disc_term_from_media_type(media_type: str | None) -> str | None:
    """Redump "Media Type" (CD-ROM, DVD-ROM-9, BD-ROM-50, UMD…) -> disc vocabulary term."""
    t = str(media_type or "").strip().upper()
    if not t:
        return None
    if t.startswith("CD"):
        return CD
    if t.startswith("DVD"):
        return DVD
    if t.startswith("BD") or "BLU" in t:
        return BD
    if t.startswith("UMD"):
        return UMD
    return None


def platform_media(system: str | None, media_type: str | None = None) -> tuple[str | None, str | None]:
    """(Platform term, Media Format term) for a dump system; (None, None) when unmapped."""
    platform, media = _TABLE.get(_key(system), (None, None))
    if platform is None and media is None:
        return None, None
    disc = disc_term_from_media_type(media_type)
    if disc and (media is None or media in _DISC_TERMS):
        media = disc
    return platform, media


def custom_vocab_value(property_id: int, vocab_id: int, term: str, *, is_public: bool = True) -> dict:
    return {"property_id": property_id, "type": f"customvocab:{int(vocab_id)}", "@value": term,
            "is_public": is_public}


def storage_link(uri: str | None, endpoint: str | None) -> tuple[str, str] | None:
    """s3://bucket/key/ -> (https URL on the Wasabi endpoint, readable label). Other URIs unchanged.

    Omeka can't link s3:// and the prefixes contain spaces ("Nintendo - Nintendo Switch"), so the
    value showed as a truncated "s3://hitsave-dumps".
    """
    from urllib.parse import quote

    if not uri:
        return None
    if not uri.startswith("s3://") or not endpoint:
        return uri, uri
    bucket, _, key = uri[len("s3://"):].partition("/")
    base = endpoint.rstrip("/")
    return f"{base}/{bucket}/{quote(key, safe='/')}", f"{base}/{bucket}/{key}"
