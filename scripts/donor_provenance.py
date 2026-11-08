"""Donor credit: `donor` in hitsave-submission.yaml -> Omeka "Donated by" (dcterms:provenance).

The intake handoff (remote lanes) and portable submission manifests both carry `donor`: one name,
a comma/semicolon-separated string, or a list. Registration adds the names to the item's
provenance values, keeping any donors already there (staff may have added them in Omeka or on the
ledger correction page).
"""
from __future__ import annotations

import re
from typing import Any, Callable

PROVENANCE = "dcterms:provenance"


def donor_names(value: Any) -> list[str]:
    items = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        for name in re.split(r"[,;]", item):
            name = " ".join(name.split())
            if name and name.casefold() not in {n.casefold() for n in out}:
                out.append(name)
    return out


def merged_provenance(
    existing_item: dict | None, donors: list[str], property_id: int, literal_value: Callable[[int, str], dict]
) -> list[dict]:
    """The item's provenance values plus each donor not already among them (case-insensitive)."""
    rows = [dict(r) for r in (existing_item or {}).get(PROVENANCE) or [] if isinstance(r, dict)]
    have = {str(r.get("@value") or "").strip().casefold() for r in rows}
    for name in donors:
        if name.casefold() not in have:
            rows.append(literal_value(property_id, name))
            have.add(name.casefold())
    return rows
