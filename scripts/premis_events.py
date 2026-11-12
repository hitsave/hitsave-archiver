"""PREMIS 3 events kept inside each bag, one XML file per event under metadata/premis/.

Files are append-only: a later change to a package adds a new event file and one
tagmanifest line, and never rewrites an earlier event. Event types come from the Library of
Congress PREMIS eventType vocabulary (id.loc.gov/vocabulary/preservation/eventType).

Fixity checks of the Wasabi copy are NOT written here; they stay in the ledger's fixity_run
table (docs/preservation-packaging-standard.md says why).
"""
from __future__ import annotations

import hashlib
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import bag_manifest
from preservation_common import load_ingest_config

PREMIS_NS = "http://www.loc.gov/premis/v3"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
DEFAULT_REPOSITORY_CODE = "hitsave"

ET.register_namespace("premis", PREMIS_NS)


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def repository_code() -> str:
    try:
        cfg = load_ingest_config()
    except FileNotFoundError:
        return DEFAULT_REPOSITORY_CODE
    return str((cfg.get("preservation") or {}).get("repository_code") or DEFAULT_REPOSITORY_CODE)


@dataclass(frozen=True)
class PremisEvent:
    event_type: str
    detail: str
    program: str
    package_id: str
    package_uuid: str = ""
    repository_code: str = DEFAULT_REPOSITORY_CODE
    outcome: str = "success"
    outcome_note: str | None = None
    when: str = field(default_factory=iso_now)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))


def event_xml(event: PremisEvent) -> str:
    if not event.package_id:
        raise ValueError("PREMIS event needs a package_id for its linking object")
    p = f"{{{PREMIS_NS}}}"
    root = ET.Element(f"{p}event", {
        "version": "3.0",
        f"{{{XSI_NS}}}schemaLocation": f"{PREMIS_NS} http://www.loc.gov/standards/premis/v3/premis.xsd",
    })
    ident = ET.SubElement(root, f"{p}eventIdentifier")
    ET.SubElement(ident, f"{p}eventIdentifierType").text = "UUID"
    ET.SubElement(ident, f"{p}eventIdentifierValue").text = event.event_id
    ET.SubElement(root, f"{p}eventType").text = event.event_type
    ET.SubElement(root, f"{p}eventDateTime").text = event.when
    detail = ET.SubElement(root, f"{p}eventDetailInformation")
    ET.SubElement(detail, f"{p}eventDetail").text = event.detail
    outcome = ET.SubElement(root, f"{p}eventOutcomeInformation")
    ET.SubElement(outcome, f"{p}eventOutcome").text = event.outcome
    if event.outcome_note:
        ET.SubElement(outcome, f"{p}eventOutcomeDetailNote").text = event.outcome_note
    for id_type, value, role in (
        ("software", event.program, "executing program"),
        ("repository code", event.repository_code, "implementer"),
    ):
        agent = ET.SubElement(root, f"{p}linkingAgentIdentifier")
        ET.SubElement(agent, f"{p}linkingAgentIdentifierType").text = id_type
        ET.SubElement(agent, f"{p}linkingAgentIdentifierValue").text = value
        ET.SubElement(agent, f"{p}linkingAgentRole").text = role
    targets = [("local package identifier", event.package_id)]
    if event.package_uuid:
        targets.append(("UUID", event.package_uuid))
    for id_type, value in targets:
        obj = ET.SubElement(root, f"{p}linkingObjectIdentifier")
        ET.SubElement(obj, f"{p}linkingObjectIdentifierType").text = id_type
        ET.SubElement(obj, f"{p}linkingObjectIdentifierValue").text = value
        ET.SubElement(obj, f"{p}linkingObjectRole").text = "target"
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def event_relpath(slug: str, event: PremisEvent, metadata_dir: str = "metadata") -> str:
    """metadata/premis/<slug>-<UTC time>-<first 8 of event id>.xml: unique, sorts by time per slug."""
    stamp = event.when.replace("-", "").replace(":", "")
    return f"{metadata_dir}/premis/{slug}-{stamp}-{event.event_id[:8]}.xml"


def write_event(bag_dir: Path, event: PremisEvent, slug: str, metadata_dir: str = "metadata") -> Path:
    """Write the event into a local bag. The caller saves the bag's tag manifests."""
    path = bag_dir / event_relpath(slug, event, metadata_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(event_xml(event), encoding="utf-8")
    return path


def add_event_file(files: dict[str, Path], work: Path, event: PremisEvent, slug: str) -> str:
    """Write the event under *work* and add it to a {bag relpath: local file} upload map."""
    rel = event_relpath(slug, event)
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(event_xml(event), encoding="utf-8")
    files[rel] = path
    return rel


def add_tagmanifest_line(tagmanifest: str, relpath: str, digest: str) -> str:
    if any(line.split(None, 1)[-1].strip() == relpath for line in tagmanifest.splitlines() if line.strip()):
        raise ValueError(f"{relpath} is already in tagmanifest-sha256.txt")
    return bag_manifest.upsert(tagmanifest, {relpath: digest})


def stage_event(tagmanifest: str, event: PremisEvent, slug: str) -> tuple[str, bytes, str]:
    """For a bag edited in place on Wasabi: (event relpath, event bytes, new tagmanifest text).

    Upload the event bytes before the tagmanifest, as every other tag file.
    """
    rel = event_relpath(slug, event)
    body = event_xml(event).encode("utf-8")
    return rel, body, add_tagmanifest_line(tagmanifest, rel, sha256_hex(body))
