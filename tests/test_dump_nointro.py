#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dump_classify import classify_dump_sip  # noqa: E402
from hitsave_dump_sidecars.nointro import (  # noqa: E402
    nointro_submission_valid,
    parse_nointro_submission,
)

SAMPLE = """\
Game name: Super Mario Land (World)

Dumper (person who dumped the ROM): tester
Dump tool: RetroDrive
Date dump was created on (YYYY-MM-DD): 2024-06-01

--ROM(s)/file(s)--
Size (bytes): 65536
CRC32: 7ad89024
MD5 hash: 7ad890245e1085493630b3a566c99c8
SHA-1 hash: 4182249530d856b6a6d1f5f2441da1d3d0b752ba
SHA-256 hash: 4182249530d856b6a6d1f5f2441da1d3d0b752ba4182249530d856b6a6d1f5f2441da1d3d0b752ba

System: Nintendo - Game Boy
"""


def test_parse_nointro_template() -> None:
    sub = parse_nointro_submission(SAMPLE)
    assert sub.game_name == "Super Mario Land (World)"
    assert sub.dat_system == "Nintendo - Game Boy"
    assert len(sub.roms) == 1
    assert sub.roms[0].size == 65536
    assert sub.roms[0].sha1 == "4182249530d856b6a6d1f5f2441da1d3d0b752ba"


def test_nointro_valid_requires_rom_hashes() -> None:
    sub = parse_nointro_submission(SAMPLE)
    assert nointro_submission_valid(sub)


def test_classify_nointro_sidecar() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sip = Path(tmp)
        (sip / "dumpinfo.txt").write_text(SAMPLE, encoding="utf-8")
        clf = classify_dump_sip(sip, package_id="mario-land-test", sort_letter="W")
        assert clf.dump_class == "no-intro"
        assert clf.dump_system == "Nintendo - Game Boy"
        assert clf.dump_category == "ROM"
        assert clf.package_storage_prefix == (
            "dumps/No-Intro/Nintendo - Game Boy/W/mario-land-test"
        )


DATOMATIC_3DS_EXAMPLE = """\
System: Nintendo - Nintendo 3DS
Game name: 0342 - 50 Classic Games 3D (Europe) (En,Fr,De,Es,It,Nl)
No-Intro record: 0342

Dumper (person who dumped the ROM): psykopat
Dump tool: GodMode9 v2.1.1
Date dump was created on (YYYY-MM-DD): 2024-07-15

--ROM(s)/file(s)--
Filename: 0342 - 50 Classic Games 3D (Europe) (En,Fr,De,Es,It,Nl).3ds
Size (bytes): 134217728
CRC32: a75fc814
MD5 hash: a7ffa10ed6592cc7d7ce56f1e1aaaf52
SHA-1 hash: 3134bc5867220e47cceec011bd0fdcfb9cef5880
SHA-256 hash: be1773a40df1516d1a00ebabee477fcae84e6a41320bc7ce49e8cb06b329b2a1
"""


def test_parse_datomatic_style_record() -> None:
    sub = parse_nointro_submission(DATOMATIC_3DS_EXAMPLE)
    assert sub.dat_system == "Nintendo - Nintendo 3DS"
    assert sub.datomatic_record_id == "0342"
    assert sub.roms[0].filename.endswith(".3ds")
    assert sub.roms[0].sha1 == "3134bc5867220e47cceec011bd0fdcfb9cef5880"
