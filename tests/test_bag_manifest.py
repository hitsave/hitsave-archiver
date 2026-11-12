"""Manifest lines: every rewrite leaves one separator style per file, the one its first line uses.

bagit-python writes payload manifests with two spaces and tag manifests with one. GNU and uutils
`sha256sum -c` read either style, but not both in one file.
"""
from __future__ import annotations

import pytest

import bag_manifest as bm

ONE = "aa bag-info.txt\nbb metadata/submission.json\n"
TWO = "aa  data/a.bin\nbb  data/b.bin\n"
MIXED = "aa bag-info.txt\nbb  metadata/premis/x.xml\ncc metadata/submission.json\n"


def test_separator_follows_first_line() -> None:
    assert bm.separator(ONE) == " "
    assert bm.separator(TWO) == "  "
    assert bm.separator(MIXED) == " "
    assert bm.separator("\n\naa  x\n") == "  "
    assert bm.separator("") == "  "


def test_entries_read_both_styles_in_order() -> None:
    assert bm.entries(MIXED) == [("aa", "bag-info.txt"), ("bb", "metadata/premis/x.xml"),
                                 ("cc", "metadata/submission.json")]
    with pytest.raises(ValueError, match="Unexpected manifest line"):
        bm.entries("justonetoken\n")


def test_add_appends_in_the_file_style() -> None:
    assert bm.add(ONE, "metadata/catalog/c.json", "dd") == ONE + "dd metadata/catalog/c.json\n"
    assert bm.add(TWO, "data/c.bin", "dd") == TWO + "dd  data/c.bin\n"
    with pytest.raises(ValueError, match="already in"):
        bm.add(ONE, "bag-info.txt", "dd")


def test_add_to_a_mixed_file_makes_it_uniform() -> None:
    out = bm.add(MIXED, "metadata/catalog/c.json", "dd")
    assert out == ("aa bag-info.txt\nbb metadata/premis/x.xml\ncc metadata/submission.json\n"
                   "dd metadata/catalog/c.json\n")


def test_upsert_replaces_and_appends_sorted() -> None:
    out = bm.upsert(MIXED, {"metadata/submission.json": "ZZ", "metadata/z.txt": "Y2", "metadata/a.txt": "Y1"})
    assert out == ("aa bag-info.txt\nbb metadata/premis/x.xml\nZZ metadata/submission.json\n"
                   "Y1 metadata/a.txt\nY2 metadata/z.txt\n")


def test_replace_requires_the_path() -> None:
    assert bm.replace(TWO, "data/b.bin", "ZZ") == "aa  data/a.bin\nZZ  data/b.bin\n"
    with pytest.raises(ValueError, match="not in"):
        bm.replace(TWO, "data/nope.bin", "ZZ")


def test_rename_keeps_digests_and_style() -> None:
    assert bm.rename(TWO, {"data/a.bin": "data/x/a.bin"}) == "aa  data/x/a.bin\nbb  data/b.bin\n"


def test_blank_lines_dropped_and_trailing_newline_kept() -> None:
    assert bm.render([("aa", "x")], " ") == "aa x\n"
    assert bm.add("aa x\n\n", "y", "bb") == "aa x\nbb y\n"
    assert bm.add("aa x", "y", "bb") == "aa x\nbb y\n"
