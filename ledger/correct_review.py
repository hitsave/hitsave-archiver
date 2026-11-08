"""Catalog correction: the item's current values, what the form changes, and the Windmill args.

The ledger only reads Omeka; Windmill's correct_catalog_item does the write (a full-item PATCH).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

FIELDS = ("title", "moby_url", "description", "developer", "publisher", "donor")
FIELD_LABELS = {
    "title": "Title",
    "moby_url": "MobyGames URL",
    "description": "Description",
    "developer": "Developers",
    "publisher": "Publishers",
    "donor": "Donated by",
}
_NAME_FIELDS = ("developer", "publisher", "donor")
_MOBY_NAME_FIELDS = ("developer", "publisher")  # what a MobyGames match brings
_TERMS = {"description": "dcterms:description", "developer": "dcterms:creator", "publisher": "dcterms:publisher",
          "donor": "dcterms:provenance"}
_EMPTY_ERRORS = {
    "title": "The title can't be empty; type the corrected title.",
    "description": (
        "The description can't be emptied here. To go back to the press-kit description, "
        "put the old text back and tick Remove the MobyGames match."
    ),
    "moby_url": (
        "To remove the MobyGames link, put the URL back and tick Remove the MobyGames match "
        "(that also removes what came with the match)."
    ),
}


@dataclass(frozen=True)
class Change:
    field: str
    before: str
    after: str
    action: str  # "set" or "clear"


def _literals(item: dict, term: str) -> list[str]:
    out = []
    for row in item.get(term) or []:
        value = str((row or {}).get("@value") or "").strip() if isinstance(row, dict) else ""
        if value:
            out.append(value)
    return out


def _moby_url(item: dict) -> str:
    for row in item.get("bibo:uri") or []:
        uri = str((row or {}).get("@id") or "").strip() if isinstance(row, dict) else ""
        if uri and not uri.startswith("s3://") and "mobygames.com" in uri.casefold():
            return uri
    return ""


def _normalize(field: str, value: str) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if field in _NAME_FIELDS:
        return ", ".join(p.strip() for p in re.split(r"[,;]", text) if p.strip())
    return text


def current_values(item: dict) -> dict[str, str]:
    titles = _literals(item, "dcterms:title")
    return {
        "title": titles[0] if titles else str(item.get("o:title") or "").strip(),
        "moby_url": _moby_url(item),
        "description": _normalize("description", "\n\n".join(_literals(item, _TERMS["description"]))),
        "developer": ", ".join(_literals(item, _TERMS["developer"])),
        "publisher": ", ".join(_literals(item, _TERMS["publisher"])),
        "donor": ", ".join(_literals(item, _TERMS["donor"])),
    }


def plan_changes(
    current: dict[str, str], form: dict[str, str], *, clear_moby: bool
) -> tuple[list[Change], list[str]]:
    changes: list[Change] = []
    errors: list[str] = []
    for field in FIELDS:
        before = _normalize(field, current.get(field, ""))
        after = _normalize(field, form.get(field, ""))
        if after == before:
            continue
        if field == "moby_url" and clear_moby:
            if after:
                errors.append("Either enter a new MobyGames URL or tick Remove the MobyGames match, not both.")
            continue
        if not after:
            if field in _EMPTY_ERRORS:
                errors.append(_EMPTY_ERRORS[field])
                continue
            changes.append(Change(field, before, "", "clear"))
        else:
            changes.append(Change(field, before, after, "set"))
    if not errors and not changes and not clear_moby:
        errors.append("Nothing to change: edit a field or tick Remove the MobyGames match.")
    return changes, errors


def changed_since(orig: dict[str, str], current: dict[str, str]) -> list[str]:
    return [f for f in FIELDS if _normalize(f, orig.get(f, "")) != _normalize(f, current.get(f, ""))]


def windmill_args(
    omeka_ref: str,
    changes: list[Change],
    *,
    clear_moby: bool,
    former_title: str,
    correction_summary: str,
    execute: bool,
) -> dict[str, Any]:
    """Body for Windmill f/hitsave/correct_catalog_item (same schema the old form sent)."""
    args: dict[str, Any] = {"omeka_ref": omeka_ref}
    for c in changes:
        if c.action == "clear":
            args[f"clear_{c.field}"] = True
        else:
            args[c.field] = c.after
    if clear_moby:
        args["clear_moby_url"] = True
    if former_title.strip():
        args["former_title"] = former_title.strip()
    if correction_summary.strip():
        args["correction_summary"] = correction_summary.strip()
    if not execute:
        args["execute"] = False
    return args


def side_effects(changes: list[Change], *, clear_moby: bool, execute: bool) -> list[str]:
    fields = {c.field for c in changes}
    out: list[str] = []
    if clear_moby:
        out.append(
            "The MobyGames link is removed, along with the release date and the MobyGames rights "
            "statement from the match."
        )
        if not fields & set(_MOBY_NAME_FIELDS):
            out.append("The developers and publishers from the match are removed too.")
        else:
            kept = [FIELD_LABELS[f].lower() for f in _MOBY_NAME_FIELDS if f not in fields]
            if kept:
                out.append(f"The {' and '.join(kept)} from the match are removed too.")
        if "description" not in fields:
            out.append("For press material, the description goes back to the text from the press kit.")
        out.append("The ledger forgets its MobyGames match for this package.")
    if fields & {"title", "moby_url"} or clear_moby:
        if execute:
            out.append("The package's details stored on Wasabi are updated to match.")
        else:
            out.append("The package's details stored on Wasabi are left as they are (upload is turned off).")
    return out
