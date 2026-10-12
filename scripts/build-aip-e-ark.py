#!/usr/bin/env python3
"""Build an E-ARK CSIP AIP directory from a game folder (representations/rep1/data/…)."""
from __future__ import annotations

import mimetypes
import shutil
import subprocess
import sys
import uuid
import xml.sax.saxutils as xml_escape
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from format_identification import (  # noqa: E402
    FormatHit,
    identify_directory,
    load_format_identification_config,
    siegfried_version,
)
from preservation_common import (  # noqa: E402
    file_grp_cit_attrs,
    iso_now,
    load_packaging_config,
    mets_file_element,
    new_id,
    package_objid,
    premis3_document,
    premis_agent_fragment,
    premis_digiprov_md_ref,
    premis_event_fragment,
    premis_file_object_fragment,
    resolve_game_config,
    sha256_file,
    should_skip,
)

sys.path.insert(0, str(ROOT / "scripts"))
from normalize_display_title import normalize_display_title  # noqa: E402

VALIDATE_SCRIPT = ROOT / "scripts" / "validate-e-ark-package.sh"
PREMIS3_NAME = "PREMIS3.xml"


@dataclass
class FileRecord:
    rel: str
    path: Path
    size: int
    sha256: str
    mime: str
    file_id: str
    object_uuid: str


def load_game_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def collect_files(source: Path, max_files: int, max_bytes: int) -> list[FileRecord]:
    records: list[FileRecord] = []
    total = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file() or should_skip(path):
            continue
        size = path.stat().st_size
        total += size
        if len(records) >= max_files:
            raise SystemExit(f"Would exceed max_files ({max_files})")
        if total > max_bytes:
            raise SystemExit(f"Would exceed max_total_bytes ({max_bytes})")
        rel = path.relative_to(source).as_posix()
        mime, _ = mimetypes.guess_type(path.name)
        records.append(
            FileRecord(
                rel=rel,
                path=path,
                size=size,
                sha256=sha256_file(path),
                mime=mime or "application/octet-stream",
                file_id=new_id("ID"),
                object_uuid=str(uuid.uuid4()),
            )
        )
    if not records:
        raise SystemExit(f"No files under {source}")
    return records


def copy_schemas(package_root: Path, packaging: dict) -> list[FileRecord]:
    src = ROOT / packaging.get("schemas_dir_in_repo", "packaging/e-ark-schemas")
    if not src.is_dir():
        raise SystemExit(f"Missing E-ARK schemas: {src}")
    dest_dir = package_root / "schemas"
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: list[FileRecord] = []
    for path in sorted(src.glob("*")):
        if not path.is_file():
            continue
        dest = dest_dir / path.name
        shutil.copy2(path, dest)
        out.append(
            FileRecord(
                rel=f"schemas/{path.name}",
                path=dest,
                size=dest.stat().st_size,
                sha256=sha256_file(dest),
                mime="application/xml",
                file_id=new_id("ID"),
                object_uuid=str(uuid.uuid4()),
            )
        )
    return out


def mets_file_from_record(rec: FileRecord, href: str, created: str) -> str:
    return mets_file_element(
        file_id=rec.file_id,
        href=href,
        created=created,
        mime=rec.mime,
        size=rec.size,
        sha256=rec.sha256,
    )


def validate_package(package_root: Path) -> None:
    script = Path("/app/scripts/validate-e-ark-package.sh")
    if not script.is_file():
        script = VALIDATE_SCRIPT
    if not script.is_file():
        raise SystemExit("validate-e-ark-package.sh not found")
    subprocess.run(["bash", str(script), str(package_root)], check=True)


def write_rep_premis3(
    premis_path: Path,
    *,
    data_files: list[FileRecord],
    format_hits: dict[str, FormatHit],
    created: str,
    agent_name: str,
    agent_version: str,
    fmt_cfg: dict,
) -> None:
    pronom_registry = str(fmt_cfg.get("pronom_registry_name") or "PRONOM")
    parts: list[str] = []
    for rec in data_files:
        hit = format_hits.get(rec.rel, FormatHit(None, None, None))
        parts.append(
            premis_file_object_fragment(
                object_uuid=rec.object_uuid,
                filepath=f"data/{rec.rel}",
                size=rec.size,
                sha256=rec.sha256,
                pronom_key=hit.pronom_key,
                format_name=hit.format_name or rec.mime,
                format_version=hit.format_version,
                pronom_registry_name=pronom_registry,
            )
        )
        digest_event_id = str(uuid.uuid4())
        parts.append(
            premis_event_fragment(
                event_id=digest_event_id,
                event_type="message digest calculation",
                event_time=created,
                outcome="success",
                detail=f"SHA-256 {rec.sha256} for data/{rec.rel}",
                agent_name=agent_name,
                agent_version=agent_version,
                linking_object=rec.object_uuid,
                linking_object_role="source",
            )
        )
    parts.append(
        premis_agent_fragment(
            agent_id="hitsave-ingest",
            agent_name=f"{agent_name} {agent_version}",
        )
    )
    if fmt_cfg.get("enabled", False):
        sf_bin = str(fmt_cfg.get("binary") or "sf")
        parts.append(
            premis_agent_fragment(
                agent_id="siegfried",
                agent_name=f"Siegfried {siegfried_version(sf_bin)}",
            )
        )
    premis_path.parent.mkdir(parents=True, exist_ok=True)
    premis_path.write_text(premis3_document(body="\n".join(parts)), encoding="utf-8")


def write_root_premis3(
    premis_path: Path,
    *,
    created: str,
    package_uuid: str,
    source: Path,
    file_count: int,
    agent_name: str,
    agent_version: str,
    repository_code: str,
) -> None:
    ingest_event_id = str(uuid.uuid4())
    body = "\n".join(
        [
            premis_event_fragment(
                event_id=ingest_event_id,
                event_type="ingestion",
                event_time=created,
                outcome="success",
                detail=(
                    f"Ingested {file_count} files from {source} into E-ARK AIP ({repository_code})"
                ),
                agent_name=agent_name,
                agent_version=agent_version,
                linking_object=package_uuid,
            ),
            premis_agent_fragment(
                agent_id="hitsave-ingest",
                agent_name=f"{agent_name} {agent_version}",
            ),
        ]
    )
    premis_path.parent.mkdir(parents=True, exist_ok=True)
    premis_path.write_text(premis3_document(body=body), encoding="utf-8")


def write_rep_mets(
    rep_mets_path: Path,
    *,
    rep_id: str,
    title: str,
    data_files: list[FileRecord],
    premis_href: str,
    premis_path: Path,
    digiprov_id: str,
    packaging: dict,
    created: str,
) -> str:
    """Write representations/{rep}/METS.xml; return rep file_id for root METS."""
    rep_file_id = new_id("ID")
    file_rows = []
    for rec in data_files:
        href = f"data/{rec.rel}"
        file_rows.append(mets_file_from_record(rec, href, created))
    files_xml = "\n".join(file_rows)
    data_grp_id = new_id("uuid")
    md_ref = premis_digiprov_md_ref(
        href=premis_href,
        premis_path=premis_path,
        created=created,
        label="Digital provenance metadata (PREMIS)",
    )
    premis_xml = f"""        <digiprovMD ID="{digiprov_id}" STATUS="CURRENT">
            {md_ref}
        </digiprovMD>
"""
    admid = digiprov_id
    profile = packaging.get("mets_profile_csip", "https://earkcsip.dilcis.eu/profile/CSIP.xml")
    cit = packaging.get("content_information_type", "MIXED")
    data_grp_cit = file_grp_cit_attrs("Data")
    content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<mets xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xmlns="http://www.loc.gov/METS/"
      xmlns:csip="https://DILCIS.eu/XML/METS/CSIPExtensionMETS"
      xmlns:xlink="http://www.w3.org/1999/xlink"
      OBJID="{xml_escape.escape(rep_id)}"
      LABEL="{xml_escape.escape(title)}"
      TYPE="Mixed"
      csip:CONTENTINFORMATIONTYPE="{cit}"
      PROFILE="{xml_escape.escape(profile)}"
      xsi:schemaLocation="http://www.loc.gov/METS/ ../../schemas/mets1_12.xsd http://www.w3.org/1999/xlink ../../schemas/xlink.xsd https://dilcis.eu/XML/METS/CSIPExtensionMETS ../../schemas/DILCISExtensionMETS.xsd">
    <metsHdr CREATEDATE="{created}" LASTMODDATE="{created}" RECORDSTATUS="NEW" csip:OAISPACKAGETYPE="AIP">
        <agent ROLE="CREATOR" TYPE="OTHER" OTHERTYPE="SOFTWARE">
            <name>HitSave build-aip-e-ark.py</name>
            <note csip:NOTETYPE="SOFTWARE VERSION">1.0</note>
        </agent>
    </metsHdr>
    <amdSec ID="{new_id('uuid')}">
{premis_xml}    </amdSec>
    <fileSec ID="{new_id('uuid')}">
        <fileGrp ID="{data_grp_id}" USE="Data"{data_grp_cit}>
{files_xml}
        </fileGrp>
    </fileSec>
    <structMap ID="{new_id('uuid')}" TYPE="PHYSICAL" LABEL="CSIP">
        <div ID="{new_id('uuid')}" TYPE="ORIGINAL" LABEL="{xml_escape.escape(rep_id)}" ADMID="{admid}">
            <div ID="{new_id('uuid')}" LABEL="Data">
                <fptr FILEID="{data_grp_id}"/>
            </div>
        </div>
    </structMap>
</mets>
"""
    rep_mets_path.parent.mkdir(parents=True, exist_ok=True)
    rep_mets_path.write_text(content, encoding="utf-8")
    rep_mets_path.stat()
    rep_meta = FileRecord(
        rel=rep_mets_path.relative_to(rep_mets_path.parents[1]).as_posix(),
        path=rep_mets_path,
        size=rep_mets_path.stat().st_size,
        sha256=sha256_file(rep_mets_path),
        mime="application/xml",
        file_id=rep_file_id,
        object_uuid=str(uuid.uuid4()),
    )
    return rep_file_id, rep_meta


def build_aip(
    source: Path,
    package_root: Path,
    *,
    title: str,
    package_uuid: str,
    preservation: dict,
    max_files: int,
    max_bytes: int,
) -> tuple[str, Path]:
    packaging = load_packaging_config()
    fmt_cfg = load_format_identification_config()
    rep_id = packaging.get("representation_id", "rep1")
    created = iso_now()
    objid = package_objid(package_uuid)
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)

    data_files = collect_files(source, max_files, max_bytes)
    rep_data = package_root / "representations" / rep_id / "data"
    for rec in data_files:
        dest = rep_data / rec.rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rec.path, dest)
        rec.path = dest

    agent_name = preservation.get("agent_name", "HitSave archive ingest")
    agent_version = preservation.get("agent_version", "build-aip-e-ark.py")
    repo = preservation.get("repository_code", "hitsave")

    format_hits = identify_directory(rep_data, fmt_cfg)

    root_premis_path = package_root / "metadata" / "preservation" / PREMIS3_NAME
    write_root_premis3(
        root_premis_path,
        created=created,
        package_uuid=package_uuid,
        source=source,
        file_count=len(data_files),
        agent_name=agent_name,
        agent_version=agent_version,
        repository_code=repo,
    )

    rep_premis_path = (
        package_root / "representations" / rep_id / "metadata" / "preservation" / PREMIS3_NAME
    )
    write_rep_premis3(
        rep_premis_path,
        data_files=data_files,
        format_hits=format_hits,
        created=created,
        agent_name=agent_name,
        agent_version=agent_version,
        fmt_cfg=fmt_cfg,
    )
    rep_digiprov_id = new_id("uuid")
    rep_premis_href = f"metadata/preservation/{PREMIS3_NAME}"

    rep_mets_path = package_root / "representations" / rep_id / "METS.xml"
    rep_file_id, rep_mets_rec = write_rep_mets(
        rep_mets_path,
        rep_id=rep_id,
        title=title,
        data_files=data_files,
        premis_href=rep_premis_href,
        premis_path=rep_premis_path,
        digiprov_id=rep_digiprov_id,
        packaging=packaging,
        created=created,
    )

    schema_files = copy_schemas(package_root, packaging)
    dc_path = package_root / "metadata" / "descriptive" / "dc.xml"
    dc_path.parent.mkdir(parents=True, exist_ok=True)
    title_esc = xml_escape.escape(title)
    dc_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>{title_esc}</dc:title>
  <dc:identifier>{xml_escape.escape(objid)}</dc:identifier>
  <dc:source>{xml_escape.escape(str(source))}</dc:source>
</metadata>
""",
        encoding="utf-8",
    )
    dc_rec = FileRecord(
        rel="metadata/descriptive/dc.xml",
        path=dc_path,
        size=dc_path.stat().st_size,
        sha256=sha256_file(dc_path),
        mime="application/xml",
        file_id=new_id("ID"),
        object_uuid=str(uuid.uuid4()),
    )

    checksum_lines = ["# SHA-256 sidecar (operator)", "# path\tdigest"]
    for rec in data_files:
        checksum_lines.append(f"{rec.rel}\t{rec.sha256}")
    checksum_path = package_root / "metadata" / "other" / "checksums-sha256.txt"
    checksum_path.parent.mkdir(parents=True, exist_ok=True)
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    doc_path = package_root / "documentation" / "README.txt"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(
        "Hit Save E-ARK AIP — press material preservation package.\n"
        f"Source: {source}\n",
        encoding="utf-8",
    )
    doc_rec = FileRecord(
        rel="documentation/README.txt",
        path=doc_path,
        size=doc_path.stat().st_size,
        sha256=sha256_file(doc_path),
        mime="text/plain",
        file_id=new_id("ID"),
        object_uuid=str(uuid.uuid4()),
    )

    # Root METS
    profile_aip = packaging.get("mets_profile_aip", packaging.get("mets_profile_csip"))
    cit = packaging.get("content_information_type", "MIXED")
    schema_grp_id = new_id("uuid")
    doc_grp_id = new_id("uuid")
    rep_grp_id = new_id("uuid")
    schema_file_rows = "\n".join(
        mets_file_from_record(rec, rec.rel, created) for rec in schema_files
    )
    doc_file_row = mets_file_from_record(doc_rec, doc_rec.rel, created)
    rep_href = f"representations/{rep_id}/METS.xml"
    rep_mets_rec.sha256 = sha256_file(rep_mets_path)
    rep_mets_rec.rel = rep_href
    rep_file_row = mets_file_from_record(rep_mets_rec, rep_href, created)

    dmd_id = new_id("uuid")
    amd_id = new_id("uuid")
    digiprov_id = new_id("uuid")
    root_premis_href = f"metadata/preservation/{PREMIS3_NAME}"
    root_md_ref = premis_digiprov_md_ref(
        href=root_premis_href,
        premis_path=root_premis_path,
        created=created,
        label="Digital provenance metadata (PREMIS)",
    )

    struct_id = new_id("uuid")
    root_div_id = new_id("uuid")
    meta_div_id = new_id("uuid")
    schema_div_id = new_id("uuid")
    doc_div_id = new_id("uuid")
    rep_outer_id = new_id("uuid")
    rep_inner_id = new_id("uuid")
    schema_grp_cit = file_grp_cit_attrs("Schemas")
    doc_grp_cit = file_grp_cit_attrs("Documentation")
    rep_grp_cit = file_grp_cit_attrs(f"Representations/{rep_id}")

    root_mets = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<mets xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xmlns="http://www.loc.gov/METS/"
      xmlns:csip="https://DILCIS.eu/XML/METS/CSIPExtensionMETS"
      xmlns:xlink="http://www.w3.org/1999/xlink"
      OBJID="{xml_escape.escape(objid)}"
      LABEL="{title_esc}"
      TYPE="Other"
      csip:OTHERTYPE="PressMaterial"
      csip:CONTENTINFORMATIONTYPE="{cit}"
      PROFILE="{xml_escape.escape(profile_aip)}"
      xsi:schemaLocation="http://www.loc.gov/METS/ schemas/mets1_12.xsd http://www.w3.org/1999/xlink schemas/xlink.xsd https://dilcis.eu/XML/METS/CSIPExtensionMETS schemas/DILCISExtensionMETS.xsd">
    <metsHdr CREATEDATE="{created}" LASTMODDATE="{created}" RECORDSTATUS="NEW" csip:OAISPACKAGETYPE="AIP">
        <agent ROLE="CREATOR" TYPE="OTHER" OTHERTYPE="SOFTWARE">
            <name>HitSave build-aip-e-ark.py</name>
            <note csip:NOTETYPE="SOFTWARE VERSION">1.0</note>
        </agent>
    </metsHdr>
    <dmdSec ID="{dmd_id}" CREATED="{created}" STATUS="CURRENT">
        <mdRef LOCTYPE="URL" MDTYPE="OTHER" OTHERMDTYPE="DC" xlink:type="simple" xlink:href="metadata/descriptive/dc.xml" MIMETYPE="application/xml" SIZE="{dc_rec.size}" CREATED="{created}" CHECKSUM="{dc_rec.sha256.upper()}" CHECKSUMTYPE="SHA-256"/>
    </dmdSec>
    <amdSec ID="{amd_id}">
        <digiprovMD ID="{digiprov_id}" STATUS="CURRENT">
            {root_md_ref}
        </digiprovMD>
    </amdSec>
    <fileSec ID="{new_id('uuid')}">
        <fileGrp ID="{schema_grp_id}" USE="Schemas"{schema_grp_cit}>
{schema_file_rows}
        </fileGrp>
        <fileGrp ID="{doc_grp_id}" USE="Documentation"{doc_grp_cit}>
{doc_file_row}
        </fileGrp>
        <fileGrp ID="{rep_grp_id}" USE="Representations/{rep_id}"{rep_grp_cit}>
{rep_file_row}
        </fileGrp>
    </fileSec>
    <structMap ID="{struct_id}" TYPE="PHYSICAL" LABEL="CSIP">
        <div ID="{root_div_id}" LABEL="{xml_escape.escape(objid)}">
            <div ID="{meta_div_id}" DMDID="{dmd_id}" ADMID="{digiprov_id}" LABEL="Metadata"/>
            <div ID="{schema_div_id}" LABEL="Schemas">
                <fptr FILEID="{schema_grp_id}"/>
            </div>
            <div ID="{doc_div_id}" LABEL="Documentation">
                <fptr FILEID="{doc_grp_id}"/>
            </div>
            <div ID="{rep_outer_id}" LABEL="Representations">
                <fptr FILEID="{rep_grp_id}"/>
                <div ID="{rep_inner_id}" LABEL="Representations/{rep_id}">
                    <mptr xlink:type="simple" xlink:href="{rep_href}" xlink:title="{rep_grp_id}" LOCTYPE="URL"/>
                </div>
            </div>
        </div>
    </structMap>
</mets>
"""
    (package_root / "METS.xml").write_text(root_mets, encoding="utf-8")
    return objid, checksum_path


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: build-aip-e-ark.py <game_config.yaml>")

    cfg_path = Path(sys.argv[1]).resolve()
    cfg = resolve_game_config(load_game_config(cfg_path), agent_version="build-aip-e-ark.py")
    source = Path(cfg["source_game_folder"]).resolve()
    package_root = Path(cfg["aip_bag_dir"]).resolve()
    if not source.is_dir():
        raise SystemExit(f"Source not found: {source}")

    title = normalize_display_title(cfg.get("omeka_item_title") or source.name)
    package_uuid = cfg.get("package_uuid") or str(uuid.uuid4())
    preservation = cfg.get("preservation") or {}
    max_files = int(cfg["max_files"])
    max_bytes = int(cfg["max_total_bytes"])

    pkg_id, checksum_path = build_aip(
        source,
        package_root,
        title=title,
        package_uuid=package_uuid,
        preservation=preservation,
        max_files=max_files,
        max_bytes=max_bytes,
    )
    validate_package(package_root)
    print(f"E-ARK AIP: {package_root}")
    print(f"  OBJID: {pkg_id}")
    print(f"  Files: {checksum_path}")


if __name__ == "__main__":
    main()
