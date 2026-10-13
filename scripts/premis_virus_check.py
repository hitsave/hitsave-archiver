"""PREMIS 3 virus-check event for BagIt metadata (ingest gate documentation)."""
from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PREMIS_NS = "http://www.loc.gov/premis/v3"
EVENT_TYPE = "virus check"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

ET.register_namespace("premis", PREMIS_NS)


@dataclass
class VirusScanReport:
    scanned_at: str
    files_scanned: int
    files_not_scanned: int
    scanner_program: str
    scanner_version: str
    virus_definitions: str | None = None
    sip_root: str = ""
    not_scanned_paths: list[str] = field(default_factory=list)


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clamav_version_info(clamd) -> tuple[str, str, str | None]:
    """Return (program, version string, optional virus DB build line)."""
    raw = clamd.version()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    line = str(raw).strip()
    program = "ClamAV"
    version = line
    virus_defs: str | None = None
    if "/" in line:
        program_part, _, rest = line.partition("/")
        program = program_part.strip() or "ClamAV"
        version = line
        virus_defs = rest.strip() or None
    return program, version, virus_defs


def _interpret_clamd_result(path: Path, result: dict | None) -> None:
    """Raise on malware; None from pyclamd means no infection (clean)."""
    if result is None:
        return
    for _scan_path, status in result.items():
        code = status[0] if status else ""
        if code == "FOUND":
            raise RuntimeError(f"Virus detected in {path}: {status[1]}")
        if code and code not in ("OK", "PASS"):
            raise RuntimeError(f"ClamAV scan error for {path}: {status}")


def scan_sip_tree(clamd, root: Path) -> VirusScanReport:
    """Scan every file under root via clamd SCAN (shared mount paths); raise on malware."""
    program, version, virus_defs = clamav_version_info(clamd)
    scanned = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        result = clamd.scan_file(str(path))
        _interpret_clamd_result(path, result)
        scanned += 1

    return VirusScanReport(
        scanned_at=iso_now(),
        files_scanned=scanned,
        files_not_scanned=0,
        scanner_program=program,
        scanner_version=version,
        virus_definitions=virus_defs,
        sip_root=str(root),
    )


def build_virus_check_premis_xml(
    report: VirusScanReport,
    *,
    package_id: str,
    package_uuid: str,
    repository_code: str,
    linking_agent: str,
) -> str:
    event_id = str(uuid.uuid4())
    root = ET.Element(
        f"{{{PREMIS_NS}}}event",
        {
            "version": "3.0",
            f"{{{XSI_NS}}}schemaLocation": (
                f"{PREMIS_NS} http://www.loc.gov/standards/premis/v3/premis.xsd"
            ),
        },
    )

    id_type = ET.SubElement(root, f"{{{PREMIS_NS}}}eventIdentifier")
    ET.SubElement(id_type, f"{{{PREMIS_NS}}}eventIdentifierType").text = "UUID"
    ET.SubElement(id_type, f"{{{PREMIS_NS}}}eventIdentifierValue").text = event_id

    ET.SubElement(root, f"{{{PREMIS_NS}}}eventType").text = EVENT_TYPE

    ET.SubElement(root, f"{{{PREMIS_NS}}}eventDateTime").text = report.scanned_at

    detail_parts = [
        f'program="{report.scanner_program}"',
        f'version="{report.scanner_version}"',
        f"filesScanned={report.files_scanned}",
    ]
    if report.virus_definitions:
        detail_parts.append(f'virusDefinitions="{report.virus_definitions}"')
    if report.sip_root:
        detail_parts.append(f'sipRoot="{report.sip_root}"')

    detail_info = ET.SubElement(root, f"{{{PREMIS_NS}}}eventDetailInformation")
    ET.SubElement(detail_info, f"{{{PREMIS_NS}}}eventDetail").text = "; ".join(detail_parts)

    outcome_info = ET.SubElement(root, f"{{{PREMIS_NS}}}eventOutcomeInformation")
    ET.SubElement(outcome_info, f"{{{PREMIS_NS}}}eventOutcome").text = "success"
    ET.SubElement(
        outcome_info, f"{{{PREMIS_NS}}}eventOutcomeDetailNote"
    ).text = "No malware detected (full ClamAV scan before BagIt build)."

    agent = ET.SubElement(root, f"{{{PREMIS_NS}}}linkingAgentIdentifier")
    ET.SubElement(agent, f"{{{PREMIS_NS}}}linkingAgentIdentifierType").text = "software"
    ET.SubElement(agent, f"{{{PREMIS_NS}}}linkingAgentIdentifierValue").text = linking_agent
    ET.SubElement(agent, f"{{{PREMIS_NS}}}linkingAgentRole").text = "executing program"

    repo_agent = ET.SubElement(root, f"{{{PREMIS_NS}}}linkingAgentIdentifier")
    ET.SubElement(repo_agent, f"{{{PREMIS_NS}}}linkingAgentIdentifierType").text = "repository code"
    ET.SubElement(repo_agent, f"{{{PREMIS_NS}}}linkingAgentIdentifierValue").text = repository_code
    ET.SubElement(repo_agent, f"{{{PREMIS_NS}}}linkingAgentRole").text = "implementer"

    obj = ET.SubElement(root, f"{{{PREMIS_NS}}}linkingObjectIdentifier")
    ET.SubElement(obj, f"{{{PREMIS_NS}}}linkingObjectIdentifierType").text = "local package identifier"
    ET.SubElement(obj, f"{{{PREMIS_NS}}}linkingObjectIdentifierValue").text = package_id
    ET.SubElement(obj, f"{{{PREMIS_NS}}}linkingObjectRole").text = "target"

    if package_uuid:
        obj2 = ET.SubElement(root, f"{{{PREMIS_NS}}}linkingObjectIdentifier")
        ET.SubElement(obj2, f"{{{PREMIS_NS}}}linkingObjectIdentifierType").text = "UUID"
        ET.SubElement(obj2, f"{{{PREMIS_NS}}}linkingObjectIdentifierValue").text = package_uuid
        ET.SubElement(obj2, f"{{{PREMIS_NS}}}linkingObjectRole").text = "target"

    ET.indent(root, space="  ")
    xml_body = ET.tostring(root, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body + "\n"


def attach_virus_check_premis(
    bag_dir: Path,
    report: VirusScanReport,
    *,
    metadata_dir: str = "metadata",
    premis_relpath: str = "premis/virus-check-event.xml",
    linking_agent: str = "ingest-folder.py",
) -> Path:
    """Write PREMIS virus-check event into the bag and refresh BagIt manifests."""
    import json

    bag_dir = bag_dir.resolve()
    submission_path = bag_dir / metadata_dir / "submission.json"
    if not submission_path.is_file():
        raise FileNotFoundError(f"Missing {submission_path} (build bag before PREMIS attach)")
    submission = json.loads(submission_path.read_text(encoding="utf-8"))
    package_id = str(submission.get("package_id") or submission.get("game_key") or "")
    package_uuid = str(submission.get("package_uuid") or "")
    repository_code = str(submission.get("repository_code") or "").strip()
    if not repository_code:
        raise ValueError("submission.json missing repository_code for PREMIS")
    if not package_id:
        raise ValueError("submission.json missing package_id for PREMIS linking object")

    out_path = bag_dir / metadata_dir / premis_relpath
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        build_virus_check_premis_xml(
            report,
            package_id=package_id,
            package_uuid=package_uuid,
            repository_code=repository_code,
            linking_agent=linking_agent,
        ),
        encoding="utf-8",
    )
    import bagit  # noqa: WPS433

    bag = bagit.Bag(str(bag_dir))
    bag.save(manifests=True)
    bag.validate()
    return out_path
