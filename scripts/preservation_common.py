"""Shared helpers for Hit Save E-ARK / METS builders."""
from __future__ import annotations

import hashlib
import uuid
import xml.sax.saxutils as xml_escape
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PREMIS_NS = "http://www.loc.gov/premis/v3"


def load_packaging_config() -> dict:
    for path in (
        Path("/config/preservation/packaging.yaml"),
        ROOT / "config" / "preservation" / "packaging.yaml",
    ):
        if path.is_file():
            data = yaml.safe_load(path.read_text()) or {}
            return data.get("preservation") or {}
    raise FileNotFoundError("packaging.yaml not found under /config/preservation/ or repo config/")


def should_skip(path: Path) -> bool:
    name = path.name
    return name.startswith(".") or name == "desktop.ini"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str = "uuid") -> str:
    return f"{prefix}-{uuid.uuid4()}"


def file_grp_cit_attrs(use: str) -> str:
    """CSIP63: eark-validator (XPath 2.0) expects OTHER + OTHERCONTENTINFORMATIONTYPE on fileGrp."""
    if use == "Schemas":
        spec = "XML Schema files (METS, CSIP, xlink, descriptive metadata)"
    elif use == "Documentation":
        spec = "Package documentation"
    elif use == "Data":
        spec = "Representation data files (mixed press material)"
    elif use == "access":
        spec = "Access derivative files (streaming-friendly copies)"
    elif use.startswith("Representations"):
        spec = "Representation METS (mixed press material)"
    else:
        spec = use
    spec_esc = xml_escape.escape(spec)
    return (
        f' csip:CONTENTINFORMATIONTYPE="OTHER" '
        f'csip:OTHERCONTENTINFORMATIONTYPE="{spec_esc}"'
    )


def mets_file_element(
    *,
    file_id: str,
    href: str,
    created: str,
    mime: str,
    size: int,
    sha256: str,
    label: str | None = None,
    group_id: str | None = None,
) -> str:
    cs = sha256.upper()
    extra = ""
    if label:
        extra += f' LABEL="{xml_escape.escape(label)}"'
    if group_id:
        extra += f' GROUPID="{xml_escape.escape(group_id)}"'
    return f"""            <file ID="{file_id}" MIMETYPE="{xml_escape.escape(mime)}" SIZE="{size}" CREATED="{created}" CHECKSUM="{cs}" CHECKSUMTYPE="SHA-256"{extra}>
                <FLocat xlink:type="simple" xlink:href="{xml_escape.escape(href)}" LOCTYPE="URL"/>
            </file>"""


def premis_event_file(
    *,
    event_id: str,
    event_type: str,
    event_time: str,
    outcome: str,
    detail: str,
    agent_name: str,
    agent_version: str,
    linking_object: str | None = None,
) -> str:
    detail_esc = xml_escape.escape(detail)
    agent_esc = xml_escape.escape(f"{agent_name} {agent_version}")
    linking = ""
    if linking_object:
        linking = f"""
  <linkingObjectIdentifier>
    <linkingObjectIdentifierType>UUID</linkingObjectIdentifierType>
    <linkingObjectIdentifierValue>{xml_escape.escape(linking_object)}</linkingObjectIdentifierValue>
    <linkingObjectRole>outcome</linkingObjectRole>
  </linkingObjectIdentifier>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<event xmlns="{PREMIS_NS}">
  <eventIdentifier>
    <eventIdentifierType>UUID</eventIdentifierType>
    <eventIdentifierValue>{xml_escape.escape(event_id)}</eventIdentifierValue>
  </eventIdentifier>
  <eventType>{xml_escape.escape(event_type)}</eventType>
  <eventDateTime>{xml_escape.escape(event_time)}</eventDateTime>
  <eventDetailInformation>
    <eventDetail>{detail_esc}</eventDetail>
  </eventDetailInformation>
  <eventOutcomeInformation>
    <eventOutcome>{xml_escape.escape(outcome.upper())}</eventOutcome>
  </eventOutcomeInformation>
  <linkingAgentIdentifier>
    <linkingAgentIdentifierType>preservation system</linkingAgentIdentifierType>
    <linkingAgentIdentifierValue>{agent_esc}</linkingAgentIdentifierValue>
  </linkingAgentIdentifier>{linking}
</event>
"""
