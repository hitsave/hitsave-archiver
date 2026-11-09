"""Rights statement and access level on Omeka items.

dcterms:rights carries a RightsStatements.org statement as a URI value. Every item gets
In Copyright (InC) unless it already has a statement: staff set the rare exceptions by hand
(for example InC-EDU on a press kit the developer gave us), and nothing here replaces them.
Other dcterms:rights values (the MobyGames credit) are kept alongside it.

dcterms:accessRights says whether the public can see the files:
  Public                                    press material
  Not public, request access if needed      dumps (the disc image is never shown)
  Disc contents public                      a dump whose disc contents are shown
"""
from __future__ import annotations

RIGHTS = "dcterms:rights"
ACCESS = "dcterms:accessRights"
IN_COPYRIGHT_URI = "http://rightsstatements.org/vocab/InC/1.0/"
IN_COPYRIGHT_LABEL = "In Copyright"

PUBLIC = "Public"
NOT_PUBLIC = "Not public, request access if needed"
CONTENTS_PUBLIC = "Disc contents public"


def is_rights_statement(row: dict) -> bool:
    uri = str(row.get("@id") or "")
    return "rightsstatements.org/" in uri


def in_copyright_value(property_id: int) -> dict:
    return {"property_id": property_id, "type": "uri", "@id": IN_COPYRIGHT_URI, "o:label": IN_COPYRIGHT_LABEL}


def merged_rights(existing_item: dict | None, new_rows: list[dict] | None, property_id: int) -> list[dict]:
    """dcterms:rights for an update.

    The other values are *new_rows* when given (e.g. a fresh MobyGames credit), else the item's
    current ones. The item's rights statement is kept; with none, InC is added.
    """
    current = [dict(r) for r in (existing_item or {}).get(RIGHTS) or [] if isinstance(r, dict)]
    statements = [r for r in current if is_rights_statement(r)]
    others = [dict(r) for r in new_rows] if new_rows is not None else [r for r in current if not is_rights_statement(r)]
    others = [r for r in others if not is_rights_statement(r)]
    return others + (statements or [in_copyright_value(property_id)])


def access_label(kind: str, *, disc_contents_public: bool = False) -> str:
    if kind == "dump":
        return CONTENTS_PUBLIC if disc_contents_public else NOT_PUBLIC
    return PUBLIC


def merged_access(existing_item: dict | None, label: str, property_id: int, *, force: bool = False) -> list[dict]:
    """The item's access value, or *label* when it has none (or when *force*)."""
    current = [dict(r) for r in (existing_item or {}).get(ACCESS) or [] if isinstance(r, dict)]
    if current and not force:
        return current
    return [{"property_id": property_id, "type": "literal", "@value": label}]


def values_for_item(
    existing_item: dict | None,
    *,
    kind: str,
    rights_pid: int,
    access_pid: int,
    disc_contents_public: bool = False,
    new_rights_rows: list[dict] | None = None,
) -> dict[str, list]:
    return {
        RIGHTS: merged_rights(existing_item, new_rights_rows, rights_pid),
        ACCESS: merged_access(existing_item, access_label(kind, disc_contents_public=disc_contents_public), access_pid),
    }


def apply_to_payload(payload: dict[str, list], existing_item: dict | None, *, kind: str, resolve_pid) -> None:
    """Add rights and access to a registration payload. *resolve_pid(term)* -> property id.

    A dcterms:rights list already in *payload* (a fresh MobyGames credit) replaces the item's other
    rights values; the item's rights statement is kept either way.
    """
    payload.update(values_for_item(
        existing_item,
        kind=kind,
        rights_pid=resolve_pid(RIGHTS),
        access_pid=resolve_pid(ACCESS),
        new_rights_rows=payload.get(RIGHTS),
    ))


def disc_contents_access_update(item: dict, *, display_public: bool, access_pid: int) -> dict[str, list]:
    """{dcterms:accessRights: [...]} after a disc contents decision, or {} when it's already right."""
    rows = merged_access(item, access_label("dump", disc_contents_public=display_public), access_pid, force=True)
    current = [{k: r.get(k) for k in ("property_id", "type", "@value")} for r in item.get(ACCESS) or []]
    return {} if current == rows else {ACCESS: rows}
