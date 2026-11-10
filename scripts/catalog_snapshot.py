"""Dated copies of a package's catalog record, kept inside its bag under metadata/catalog/.

Each copy is one JSON file, catalog-<UTC time>.json, made from the item's Omeka record. A copy
is only ever added: when the record changes, a new dated file goes next to the older ones, with
a PREMIS "metadata modification" event and tagmanifest lines for both. The newest file is the
current record; the older ones show what the record said before.

The copy is meant to be read without Omeka: values keep their Dublin Core / BIBO term names,
links to other items carry that item's ARK and title, and item sets and the resource template
are named, not just numbered.
"""
from __future__ import annotations

import hashlib
import json
import re

from premis_events import PremisEvent, add_tagmanifest_line, event_relpath, event_xml, sha256_hex

SNAPSHOT_FORMAT = "hitsave-catalog-snapshot/1"
ARK_NAAN = "78322"
CATALOG_DIR = "metadata/catalog"
EVENT_SLUG = "catalog-snapshot"

ABOUT = (
    "A copy of this package's catalog record at archive.hitsave.org, taken at taken_at. "
    "Every file in metadata/catalog/ is one copy; the newest file is the current record and "
    "older files show earlier versions. Values are keyed by Dublin Core terms "
    "(dcterms: http://purl.org/dc/terms/) and BIBO (bibo: http://purl.org/ontology/bibo/). "
    "A value with public false is shown to staff only. The ARK is the record's permanent "
    "identifier; its last number is the Omeka item id."
)

# Fields that say when the copy was made, not what the record says.
_VOLATILE = ("taken_at", "omeka_modified")
_SNAPSHOT_RE = re.compile(r"^metadata/catalog/catalog-\d{8}T\d{6}Z\.json$")


def ark(item_id: int) -> str:
    return f"ark:/{ARK_NAAN}/{int(item_id)}"


def _dt(value) -> str | None:
    if isinstance(value, dict):
        return value.get("@value")
    return value or None


def _ref_id(ref) -> int | None:
    if isinstance(ref, dict) and ref.get("o:id") is not None:
        return int(ref["o:id"])
    return None


def _value(raw: dict) -> dict:
    kind = str(raw.get("type") or "")
    out: dict = {"type": kind}
    if kind.startswith("resource"):
        rid = raw.get("value_resource_id")
        out["item_id"] = int(rid) if rid is not None else None
        if raw.get("value_resource_name") in (None, "items") and rid is not None:
            out["ark"] = ark(rid)
        out["title"] = raw.get("display_title")
    elif kind == "uri":
        out["uri"] = raw.get("@id")
        if raw.get("o:label"):
            out["label"] = raw["o:label"]
    else:
        out["value"] = raw.get("@value")
    if raw.get("@language"):
        out["language"] = raw["@language"]
    out["public"] = bool(raw.get("is_public", True))
    return out


def _values(item: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for key in sorted(item):
        rows = item[key]
        if ":" not in key or key.startswith("o:") or not isinstance(rows, list):
            continue
        if not all(isinstance(r, dict) and "property_id" in r for r in rows):
            continue
        out[key] = [_value(r) for r in rows]
    return {k: v for k, v in out.items() if v}


def build_snapshot(item: dict, *, package_id: str, taken_at: str, names: dict) -> dict:
    """Snapshot of an Omeka item (JSON-LD from the API). *names* maps ids to labels:
    {"item_sets": {id: title}, "templates": {id: label}, "classes": {id: term}}."""
    item_id = int(item["o:id"])
    template_id = _ref_id(item.get("o:resource_template"))
    class_id = _ref_id(item.get("o:resource_class"))
    return {
        "format": SNAPSHOT_FORMAT,
        "about": ABOUT,
        "taken_at": taken_at,
        "package_id": package_id,
        "omeka_item_id": item_id,
        "ark": ark(item_id),
        "api_url": item.get("@id"),
        "public": bool(item.get("o:is_public")),
        "created": _dt(item.get("o:created")),
        "omeka_modified": _dt(item.get("o:modified")),
        "resource_class": None if class_id is None else {
            "id": class_id, "term": names.get("classes", {}).get(class_id)},
        "resource_template": None if template_id is None else {
            "id": template_id, "label": names.get("templates", {}).get(template_id)},
        "item_sets": [
            {"id": sid, "title": names.get("item_sets", {}).get(sid)}
            for sid in (_ref_id(r) for r in item.get("o:item_set") or []) if sid is not None
        ],
        "sites": [sid for sid in (_ref_id(r) for r in item.get("o:site") or []) if sid is not None],
        "media": [mid for mid in (_ref_id(r) for r in item.get("o:media") or []) if mid is not None],
        "values": _values(item),
    }


def _content(snapshot: dict) -> dict:
    return {k: v for k, v in snapshot.items() if k not in _VOLATILE}


def content_digest(snapshot: dict) -> str:
    """SHA-256 of what the record says, ignoring when the copy was taken."""
    body = json.dumps(_content(snapshot), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def changed_fields(old: dict, new: dict) -> list[str]:
    """Top-level fields and value terms that differ (sorted), e.g. ["dcterms:title", "public"]."""
    a, b = _content(old), _content(new)
    changed = {k for k in set(a) | set(b) if k != "values" and a.get(k) != b.get(k)}
    av, bv = a.get("values") or {}, b.get("values") or {}
    changed |= {k for k in set(av) | set(bv) if av.get(k) != bv.get(k)}
    return sorted(changed)


def snapshot_relpath(taken_at: str) -> str:
    stamp = taken_at.replace("-", "").replace(":", "")
    return f"{CATALOG_DIR}/catalog-{stamp}.json"


def latest_snapshot_relpath(tagmanifest: str) -> str | None:
    paths = [
        parts[1].strip()
        for parts in (line.split(None, 1) for line in tagmanifest.splitlines() if line.strip())
        if len(parts) == 2 and _SNAPSHOT_RE.match(parts[1].strip())
    ]
    return max(paths) if paths else None


def snapshot_bytes(snapshot: dict) -> bytes:
    return (json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def stage_snapshot(
    tagmanifest: str,
    snapshot: dict,
    *,
    previous: dict | None,
    previous_relpath: str | None = None,
    program: str = "sync-catalog-snapshots.py",
    package_uuid: str = "",
    repository_code: str = "hitsave",
) -> tuple[dict[str, bytes], str]:
    """Files to add ({bag relpath: bytes}, snapshot first, then its event) and the new tagmanifest.

    Upload the files before the tagmanifest, as every other tag file.
    """
    rel = snapshot_relpath(snapshot["taken_at"])
    body = snapshot_bytes(snapshot)
    if previous is None:
        detail = f"Added {rel}, the first copy of the catalog record (Omeka item {snapshot['omeka_item_id']}) in this bag."
    else:
        prev_rel = previous_relpath or snapshot_relpath(previous["taken_at"])
        fields = ", ".join(changed_fields(previous, snapshot)) or "none"
        detail = (
            f"Added {rel}: the catalog record changed since {prev_rel} (changed: {fields}). "
            "Earlier copies kept."
        )
    event = PremisEvent(
        event_type="metadata modification",
        detail=detail + " Payload unchanged.",
        program=program,
        package_id=str(snapshot["package_id"]),
        package_uuid=package_uuid,
        repository_code=repository_code,
        when=snapshot["taken_at"],
    )
    event_rel = event_relpath(EVENT_SLUG, event)
    event_body = event_xml(event).encode("utf-8")
    tm = add_tagmanifest_line(tagmanifest, rel, sha256_hex(body))
    tm = add_tagmanifest_line(tm, event_rel, sha256_hex(event_body))
    return {rel: body, event_rel: event_body}, tm
