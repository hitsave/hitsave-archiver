"""Which dumps get their disc contents shown on the public site.

Data discs (PC, Mac and other computer CDs/DVDs) can hold videos, pictures and documents worth
showing. Whether to show them depends on what the disc is:

- labelled press kit (Redump Edition, Comments or the contributor's folder name): public
- promotional or editorial (Multimedia, Video, Audio, Bonus Discs, Coverdiscs) or unknown: review
  queue; staff decide in the ledger
- retail or unreleased software (Games, Demos, Applications, Educational, Add-Ons, Preproduction):
  no public content, even when labelled as a press kit
- console and video discs: not data discs, skipped

The result is stored on the ledger row (game_ingest.disc_contents_*); staff can change the display
later (review -> public / none).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Display decisions
PUBLIC, REVIEW, NONE, SKIP = "public", "review", "none", "skip"

# Redump "System" values for computers (data discs with a browsable file system).
COMPUTER_SYSTEMS = frozenset({
    "ibm pc compatible",
    "apple macintosh",
    "nec pc-98 series",
    "nec pc-88 series",
    "fujitsu fm towns series",
    "sharp x68000",
    "acorn archimedes",
    "commodore amiga",
    "commodore amiga cd",
})

# Redump categories (redump.info disc list filter).
NO_PUBLIC_CATEGORIES = frozenset({
    "games", "demos", "applications", "educational", "add-ons", "preproduction",
})
REVIEW_CATEGORIES = frozenset({
    "multimedia", "video", "audio", "bonus discs", "coverdiscs", "coverdisc",
})

# A press kit label. Deliberately narrow: "press release(s)" often describes a disc's contents.
_PRESS_KIT = re.compile(
    r"\bpress[\s_-]*(?:kit|disc|cd)s?\b|\belectronic[\s_-]*press[\s_-]*kit\b|\bEPK\b|\bmedia[\s_-]*kit\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DiscContentsDecision:
    disc_class: str  # press_kit | promotional | retail | not_data_disc
    display: str  # public | review | none | skip
    reasons: list[str] = field(default_factory=list)

    @property
    def extract(self) -> bool:
        """Whether the sweep extracts the disc's files (public now, or held for review)."""
        return self.display in (PUBLIC, REVIEW)


def _norm(value: str | None) -> str:
    return " ".join(str(value or "").split()).casefold()


def press_kit_label(*texts: str | None) -> str | None:
    """The first text that labels the disc a press kit, or None."""
    for text in texts:
        if text and _PRESS_KIT.search(text):
            return " ".join(text.split())
    return None


def classify(
    *,
    system: str | None,
    category: str | None,
    edition: str | None = None,
    comments: str | None = None,
    folder_name: str | None = None,
) -> DiscContentsDecision:
    """Decide from the Redump sidecar fields (System, Category, Edition/Release, Comments) and the
    contributor's folder name."""
    sys_n, cat_n = _norm(system), _norm(category)
    if sys_n not in COMPUTER_SYSTEMS:
        return DiscContentsDecision("not_data_disc", SKIP, [f"System {system or '(none)'!r} is not a computer"])
    label = None
    for source, text in (("Edition", edition), ("Comments", comments), ("folder name", folder_name)):
        if press_kit_label(text):
            label = f"{source}: {' '.join(str(text).split())[:120]}"
            break
    if cat_n in NO_PUBLIC_CATEGORIES:
        reasons = [f"Category {category!r}: retail or unreleased software, no public content"]
        if label:
            reasons.append(f"press kit label ignored ({label})")
        return DiscContentsDecision("retail", NONE, reasons)
    if label:
        return DiscContentsDecision("press_kit", PUBLIC, [f"labelled press kit ({label})"])
    if cat_n in REVIEW_CATEGORIES:
        return DiscContentsDecision("promotional", REVIEW, [f"Category {category!r}: needs review"])
    return DiscContentsDecision("promotional", REVIEW, [f"Category {category or '(none)'!r}: unknown, needs review"])


# Redump pages say "Edition/Release"; MPF's !submissionInfo.txt says "Editions".
EDITION_FIELDS = ("Edition/Release", "Edition", "Editions")


def edition_field(fields: dict[str, str]) -> str | None:
    """The disc's edition text from parsed sidecar fields, whichever spelling the sidecar uses."""
    for key in EDITION_FIELDS:
        if fields.get(key):
            return fields[key]
    return None


def classify_submission(fields: dict[str, str], *, folder_name: str | None = None) -> DiscContentsDecision:
    """classify() from parsed Redump sidecar fields (hitsave_dump_sidecars.redump)."""
    return classify(
        system=fields.get("System"),
        category=fields.get("Category"),
        edition=edition_field(fields),
        comments=fields.get("Comments"),
        folder_name=folder_name,
    )
