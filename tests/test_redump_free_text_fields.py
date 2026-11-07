"""Redump Comments / Contents are free text over several untabbed lines (MPF output)."""
from __future__ import annotations

from hitsave_dump_sidecars.redump import parse_submission_info

SIDECAR = (
    "Common Disc Info:\n"
    "\tTitle: Titus / Interplay / Virgin Interactive Entertainment\n"
    "\tSystem: IBM PC compatible\n"
    "\tCategory: Multimedia\n"
    "\tComments:\n"
    "\n"
    "<b>Logs Link</b>: [Please provide a link to your logs here]\n"
    "[T:VOL] 2002DPK\n"
    "Press disc released at E3 2002.\n"
    "[T:ALT] Titus & Interplay & Virgin\n"
    "\n"
    "\tContents:\n"
    "\n"
    "Videos:\n"
    "Falcone: Into the Maelstrom (0:46)\n"
    "Extras:\n"
    "Concept Artwork\n"
    "\n"
    "Version and Editions:\n"
    "\tVersion: \n"
    "\tEdition/Release: Press Kit\n"
    "\n"
    "Extras:\n"
    "\tPrimary Volume Descriptor (PVD):\n"
    "\n"
    "0320 : 20 20 20\n"
    "\n"
    "Copy Protection:\n"
    "\tCopy Protection: None found\n"
)


def test_comments_and_contents_keep_their_lines() -> None:
    sub = parse_submission_info(SIDECAR)
    comments = sub.fields["Comments"]
    assert "Press disc released at E3 2002." in comments and "[T:VOL] 2002DPK" in comments
    assert sub.fields["Contents"].splitlines() == [
        "Videos:", "Falcone: Into the Maelstrom (0:46)", "Extras:", "Concept Artwork",
    ]
    assert not any(k.startswith(("[T", "<b>", "Falcone", "Videos")) for k in sub.fields)


def test_fields_after_free_text_still_parse() -> None:
    sub = parse_submission_info(SIDECAR)
    assert sub.fields["Edition/Release"] == "Press Kit"
    assert sub.fields["Copy Protection"] == "None found"
    assert sub.sections["Version and Editions"]["Edition/Release"] == "Press Kit"
    assert "Primary Volume Descriptor (PVD)" in sub.sections["Extras"]
    assert sub.alternate_titles == ["Titus & Interplay & Virgin"]
