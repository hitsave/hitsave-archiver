"""Which dumps get their disc contents shown: press kits public, promotional discs reviewed,
retail software never, consoles skipped."""
from __future__ import annotations

import disc_contents_policy as p


def test_labelled_press_kit_is_public() -> None:
    d = p.classify(system="IBM PC compatible", category="Multimedia", edition="Press Kit")
    assert (d.disc_class, d.display, d.extract) == ("press_kit", "public", True)
    assert "Edition: Press Kit" in d.reasons[0]


def test_press_kit_label_from_comments_or_folder_name() -> None:
    by_comment = p.classify(system="IBM PC compatible", category="Multimedia",
                            comments="[T:VOL] 2002DPK\nPress disc released at E3 2002.")
    assert by_comment.display == "public" and "Comments" in by_comment.reasons[0]
    by_folder = p.classify(system="Apple Macintosh", category="",
                           folder_name="09 - Titus - Interplay - Virgin Interactive Entertainment (Press Kit)")
    assert by_folder.display == "public"
    assert p.classify(system="IBM PC compatible", category="Multimedia", edition="EPK").display == "public"


def test_press_release_in_comments_is_not_a_label() -> None:
    d = p.classify(system="IBM PC compatible", category="Bonus Discs", comments="Contains press releases.")
    assert (d.disc_class, d.display) == ("promotional", "review")


def test_promotional_categories_go_to_review() -> None:
    for cat in ("Multimedia", "Video", "Bonus Discs", "Coverdiscs", "Audio", "", "Something New"):
        d = p.classify(system="IBM PC compatible", category=cat, edition="Original")
        assert d.display == "review" and d.extract, cat


def test_retail_software_never_public_even_if_labelled() -> None:
    for cat in ("Games", "Demos", "Applications", "Educational", "Add-Ons", "Preproduction"):
        d = p.classify(system="IBM PC compatible", category=cat, edition="Press Kit")
        assert (d.disc_class, d.display, d.extract) == ("retail", "none", False), cat
        assert any("ignored" in r for r in d.reasons)


def test_console_and_video_discs_are_skipped() -> None:
    for system in ("Sony PlayStation", "DVD-Video", "Audio CD", "Nintendo GameCube", None):
        d = p.classify(system=system, category="Multimedia", edition="Press Kit")
        assert (d.display, d.extract) == ("skip", False)


def test_classify_submission_reads_redump_fields() -> None:
    fields = {"System": "IBM PC compatible", "Category": "Multimedia", "Edition/Release": "Press Kit",
              "Comments": "Press disc released at E3 2002."}
    assert p.classify_submission(fields).display == "public"
