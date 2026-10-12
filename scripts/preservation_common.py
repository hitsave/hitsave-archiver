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
PREMIS_VERSION = "3.0"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"


def load_ingest_config() -> dict:
    for path in (
        Path("/config/preservation/ingest.yaml"),
        ROOT / "config" / "preservation" / "ingest.yaml",
    ):
        if path.is_file():
            data = yaml.safe_load(path.read_text()) or {}
            return data
    raise FileNotFoundError("ingest.yaml not found under /config/preservation/ or repo config/")


def batch_output_dirs(batch_key: str, ingest: dict, out_cfg: dict | None = None) -> tuple[Path, Path, Path]:
    """Default batch DIP/AIP/staging dirs under paths.output_root (container paths)."""
    out_cfg = out_cfg or {}
    paths = ingest["paths"]
    root = Path(paths["output_root"])
    dip_sub = paths.get("dip_subdir", "dip")
    aip_sub = paths.get("aip_subdir", "aip")
    rel = Path("batch") / batch_key
    dip_dir = Path(out_cfg.get("dip_dir", root / dip_sub / rel))
    aip_dir = Path(out_cfg.get("aip_dir", root / aip_sub / rel))
    staging_root = Path(out_cfg.get("staging_dir", root / ".staging" / rel))
    return dip_dir, aip_dir, staging_root


def resolve_game_config(cfg: dict, *, agent_version: str | None = None) -> dict:
    """Apply ingest.yaml paths, defaults, and preservation metadata to a game config."""
    ingest = load_ingest_config()
    paths = ingest["paths"]
    out = dict(cfg)
    press_root = Path(paths["press_material_root"])
    output_root = Path(paths["output_root"])
    dip_sub = paths.get("dip_subdir", "dip")
    aip_sub = paths.get("aip_subdir", "aip")

    if out.get("source_subpath") and not out.get("source_game_folder"):
        sub = str(out["source_subpath"]).strip().strip("/")
        if not sub:
            raise ValueError("source_subpath must not be empty")
        out["source_game_folder"] = str(press_root / sub)

    game_key = out.get("game_key")
    if not game_key and out.get("source_game_folder"):
        game_key = Path(out["source_game_folder"]).name
        out["game_key"] = game_key

    batch_key = out.get("batch_key")
    batch_segment = Path("batch") / batch_key if batch_key else None

    if game_key and not out.get("output_tar"):
        dip_base = output_root / dip_sub
        if batch_segment:
            dip_base = dip_base / batch_segment
        out["output_tar"] = str(dip_base / f"{game_key}.tar")

    if game_key and not out.get("staging_dir"):
        stag_base = output_root / ".staging"
        if batch_segment:
            stag_base = stag_base / batch_segment
        out["staging_dir"] = str(stag_base / game_key)

    if game_key and not out.get("aip_bag_dir"):
        aip_base = output_root / aip_sub
        if batch_segment:
            aip_base = aip_base / batch_segment
        out["aip_bag_dir"] = str(aip_base / game_key)

    defaults = ingest.get("defaults") or {}
    out.setdefault("max_files", defaults.get("max_files", 5000))
    out.setdefault("max_total_bytes", defaults.get("max_total_bytes", 5368709120))

    pres = dict(ingest.get("preservation") or {})
    pres.update(out.get("preservation") or {})
    if agent_version:
        pres["agent_version"] = agent_version
    out["preservation"] = pres
    return out


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


def package_objid(identifier: str) -> str:
    """METS OBJID with recommended urn:uuid: prefix when value is a bare UUID."""
    value = identifier.strip()
    if value.lower().startswith("urn:"):
        return value
    return f"urn:uuid:{value}"


def premis_digiprov_md_ref(
    *,
    href: str,
    premis_path: Path,
    created: str,
    label: str | None = None,
) -> str:
    """Single digiprovMD/mdRef for PREMIS 3 (AIPM6–7)."""
    pcs = sha256_file(premis_path).upper()
    label_attr = f' LABEL="{xml_escape.escape(label)}"' if label else ""
    return (
        f'<mdRef LOCTYPE="URL" xlink:type="simple" xlink:href="{xml_escape.escape(href)}" '
        f'MDTYPE="PREMIS" MDTYPEVERSION="{PREMIS_VERSION}" MIMETYPE="text/xml" '
        f'SIZE="{premis_path.stat().st_size}" CREATED="{created}" '
        f'CHECKSUM="{pcs}" CHECKSUMTYPE="SHA-256"{label_attr}/>'
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


def premis_event_fragment(
    *,
    event_id: str,
    event_type: str,
    event_time: str,
    outcome: str,
    detail: str,
    agent_name: str,
    agent_version: str,
    linking_object: str | None = None,
    linking_object_role: str = "outcome",
) -> str:
    detail_esc = xml_escape.escape(detail)
    agent_esc = xml_escape.escape(f"{agent_name} {agent_version}")
    linking = ""
    if linking_object:
        linking = f"""
  <linkingObjectIdentifier>
    <linkingObjectIdentifierType>UUID</linkingObjectIdentifierType>
    <linkingObjectIdentifierValue>{xml_escape.escape(linking_object)}</linkingObjectIdentifierValue>
    <linkingObjectRole>{xml_escape.escape(linking_object_role)}</linkingObjectRole>
  </linkingObjectIdentifier>"""
    return f"""  <event>
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
  </event>"""


def premis_file_object_fragment(
    *,
    object_uuid: str,
    filepath: str,
    size: int,
    sha256: str,
    pronom_key: str | None,
    format_name: str | None,
    format_version: str | None,
    pronom_registry_name: str = "PRONOM",
) -> str:
    xml_id = f"id-{object_uuid}"
    format_block = ""
    if pronom_key or format_name:
        designation = ""
        if format_name:
            version_part = (
                f"\n          <formatVersion>{xml_escape.escape(format_version)}</formatVersion>"
                if format_version
                else ""
            )
            designation = f"""        <formatDesignation>
          <formatName>{xml_escape.escape(format_name)}</formatName>{version_part}
        </formatDesignation>"""
        registry = ""
        if pronom_key:
            registry = f"""        <formatRegistry>
          <formatRegistryName>{xml_escape.escape(pronom_registry_name)}</formatRegistryName>
          <formatRegistryKey>{xml_escape.escape(pronom_key)}</formatRegistryKey>
          <formatRegistryRole>identification</formatRegistryRole>
        </formatRegistry>"""
        format_block = f"""
      <format>
{designation}
{registry}
      </format>"""
    return f"""  <object xsi:type="file" xmlID="{xml_escape.escape(xml_id)}">
    <objectIdentifier>
      <objectIdentifierType>UUID</objectIdentifierType>
      <objectIdentifierValue>{xml_escape.escape(object_uuid)}</objectIdentifierValue>
    </objectIdentifier>
    <objectCharacteristics>
      <compositionLevel>0</compositionLevel>
      <fixity>
        <messageDigestAlgorithm>SHA-256</messageDigestAlgorithm>
        <messageDigest>{xml_escape.escape(sha256.lower())}</messageDigest>
        <messageDigestOriginator>HitSave build-aip-e-ark.py</messageDigestOriginator>
      </fixity>
      <size>{size}</size>{format_block}
    </objectCharacteristics>
    <originalName>{xml_escape.escape(filepath)}</originalName>
  </object>"""


def premis_agent_fragment(*, agent_id: str, agent_name: str, agent_type: str = "software") -> str:
    return f"""  <agent>
    <agentIdentifier>
      <agentIdentifierType>local</agentIdentifierType>
      <agentIdentifierValue>{xml_escape.escape(agent_id)}</agentIdentifierValue>
    </agentIdentifier>
    <agentName>{xml_escape.escape(agent_name)}</agentName>
    <agentType>{xml_escape.escape(agent_type)}</agentType>
  </agent>"""


def premis3_document(*, body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<premis xmlns="{PREMIS_NS}" xmlns:xsi="{XSI_NS}" version="{PREMIS_VERSION}" xsi:schemaLocation="{PREMIS_NS} schemas/premis-v3-0.xsd">
{body}
</premis>
"""


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
    """Standalone event XML (legacy DIP builder)."""
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
