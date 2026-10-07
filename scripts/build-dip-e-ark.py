#!/usr/bin/env python3
"""Build an E-ARK CSIP DIP as a .tar (representations/rep1/data + optional access copies)."""
from __future__ import annotations

import mimetypes
import shutil
import subprocess
import sys
import tarfile
import uuid
import xml.sax.saxutils as xml_escape
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import importlib.util

_aip_spec = importlib.util.spec_from_file_location(
    "build_aip_e_ark", ROOT / "scripts" / "build-aip-e-ark.py"
)
_aip = importlib.util.module_from_spec(_aip_spec)
sys.modules[_aip_spec.name] = _aip
assert _aip_spec.loader is not None
_aip_spec.loader.exec_module(_aip)
FileRecord = _aip.FileRecord
collect_files = _aip.collect_files
copy_schemas = _aip.copy_schemas
load_game_config = _aip.load_game_config
mets_file_from_record = _aip.mets_file_from_record

from preservation_common import (  # noqa: E402
    file_grp_cit_attrs,
    iso_now,
    load_packaging_config,
    mets_file_element,
    new_id,
    premis_event_file,
    sha256_file,
)
from normalize_display_title import normalize_display_title  # noqa: E402

DEFAULT_SETTINGS = ROOT / "config" / "omeka-test" / "settings.yaml"
DEFAULT_VIDEO_EXTENSIONS = [
    "mov",
    "avi",
    "mkv",
    "wmv",
    "mp4",
    "m4v",
    "webm",
    "mpg",
    "mpeg",
]
VALIDATE_SCRIPT = ROOT / "scripts" / "validate-e-ark-package.sh"


@dataclass
class AccessRecord:
    rel: str
    path: Path
    size: int
    sha256: str
    file_id: str
    group_id: str
    object_name: str


def load_video_access_cfg(game_cfg: dict) -> dict:
    defaults = {
        "enabled": True,
        "ffmpeg_path": "ffmpeg",
        "max_height": 720,
        "crf": 23,
        "preset": "fast",
        "copy_mp4_without_transcode": True,
        "extensions": list(DEFAULT_VIDEO_EXTENSIONS),
    }
    if DEFAULT_SETTINGS.is_file():
        settings = yaml.safe_load(DEFAULT_SETTINGS.read_text()) or {}
        dip = settings.get("dip_viewer") or {}
        file_cfg = dip.get("video_access") or {}
        if isinstance(file_cfg, dict):
            defaults.update(file_cfg)
    override = game_cfg.get("video_access") or {}
    if isinstance(override, dict):
        defaults.update(override)
    return defaults


def is_video_candidate(path: Path, cfg: dict) -> bool:
    ext = path.suffix.lower().lstrip(".")
    return ext in (cfg.get("extensions") or DEFAULT_VIDEO_EXTENSIONS)


def create_access_copy(staged_original: Path, access_dir: Path, group_id: str, cfg: dict) -> AccessRecord | None:
    if not cfg.get("enabled", True):
        return None
    access_dir.mkdir(parents=True, exist_ok=True)
    file_uuid = group_id.removeprefix("Group-")
    object_name = f"{file_uuid}-access.mp4"
    out_path = access_dir / object_name
    ext = staged_original.suffix.lower()
    if ext == ".mp4" and cfg.get("copy_mp4_without_transcode", True):
        shutil.copy2(staged_original, out_path)
    else:
        ffmpeg = str(cfg.get("ffmpeg_path", "ffmpeg"))
        max_height = int(cfg.get("max_height", 720))
        crf = str(cfg.get("crf", 23))
        preset = str(cfg.get("preset", "fast"))
        scale = f"scale=-2:'min({max_height},ih)'"
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(staged_original),
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            crf,
            "-vf",
            scale,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"Warning: access copy skipped for {staged_original.name}: {exc}", file=sys.stderr)
            out_path.unlink(missing_ok=True)
            return None
    if not out_path.is_file():
        return None
    return AccessRecord(
        rel="",
        path=out_path,
        size=out_path.stat().st_size,
        sha256=sha256_file(out_path),
        file_id=new_id("ID"),
        group_id=group_id,
        object_name=object_name,
    )


def write_dip_rep_mets(
    rep_mets_path: Path,
    *,
    rep_id: str,
    title: str,
    data_files: list[FileRecord],
    access_files: list[AccessRecord],
    packaging: dict,
    created: str,
) -> FileRecord:
    data_grp_id = new_id("uuid")
    data_rows = []
    for rec in data_files:
        group_id = f"Group-{rec.object_uuid}"
        data_rows.append(
            mets_file_element(
                file_id=rec.file_id,
                href=f"data/{rec.rel}",
                created=created,
                mime=rec.mime,
                size=rec.size,
                sha256=rec.sha256,
                label=rec.rel,
                group_id=group_id,
            )
        )
    access_grp = ""
    if access_files:
        access_rows = []
        for acc in access_files:
            label = f"{acc.rel} (access copy)" if acc.rel else "access copy"
            access_rows.append(
                mets_file_element(
                    file_id=acc.file_id,
                    href=f"access/{acc.object_name}",
                    created=created,
                    mime="video/mp4",
                    size=acc.size,
                    sha256=acc.sha256,
                    label=label,
                    group_id=acc.group_id,
                )
            )
        access_grp = f"""        <fileGrp ID="{new_id('uuid')}" USE="access"{file_grp_cit_attrs('access')}>
{chr(10).join(access_rows)}
        </fileGrp>
"""
    profile = packaging.get("mets_profile_csip", "https://earkcsip.dilcis.eu/profile/CSIP.xml")
    cit = packaging.get("content_information_type", "MIXED")
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
    <metsHdr CREATEDATE="{created}" LASTMODDATE="{created}" RECORDSTATUS="NEW">
        <agent ROLE="CREATOR" TYPE="OTHER" OTHERTYPE="SOFTWARE">
            <name>HitSave build-dip-e-ark.py</name>
            <note csip:NOTETYPE="SOFTWARE VERSION">1.0</note>
        </agent>
    </metsHdr>
    <fileSec ID="{new_id('uuid')}">
        <fileGrp ID="{data_grp_id}" USE="Data"{file_grp_cit_attrs('Data')}>
{chr(10).join(data_rows)}
        </fileGrp>
{access_grp}    </fileSec>
    <structMap ID="{new_id('uuid')}" TYPE="PHYSICAL" LABEL="CSIP">
        <div ID="{new_id('uuid')}" TYPE="ORIGINAL" LABEL="{xml_escape.escape(rep_id)}">
            <div ID="{new_id('uuid')}" LABEL="Data">
                <fptr FILEID="{data_grp_id}"/>
            </div>
        </div>
    </structMap>
</mets>
"""
    rep_mets_path.parent.mkdir(parents=True, exist_ok=True)
    rep_mets_path.write_text(content, encoding="utf-8")
    rep_file_id = new_id("ID")
    return FileRecord(
        rel=rep_mets_path.relative_to(rep_mets_path.parents[1]).as_posix(),
        path=rep_mets_path,
        size=rep_mets_path.stat().st_size,
        sha256=sha256_file(rep_mets_path),
        mime="application/xml",
        file_id=rep_file_id,
        object_uuid=str(uuid.uuid4()),
    )


def write_root_mets_dip(
    package_root: Path,
    *,
    package_uuid: str,
    title: str,
    rep_id: str,
    rep_mets_rec: FileRecord,
    schema_files: list[FileRecord],
    doc_rec: FileRecord,
    dc_rec: FileRecord,
    ingest_path: Path,
    packaging: dict,
    created: str,
) -> None:
    profile_dip = packaging.get("mets_profile_dip", packaging.get("mets_profile_csip"))
    cit = packaging.get("content_information_type", "MIXED")
    title_esc = xml_escape.escape(title)
    schema_grp_id = new_id("uuid")
    doc_grp_id = new_id("uuid")
    rep_grp_id = new_id("uuid")
    schema_file_rows = "\n".join(mets_file_from_record(rec, rec.rel, created) for rec in schema_files)
    doc_file_row = mets_file_from_record(doc_rec, doc_rec.rel, created)
    rep_href = f"representations/{rep_id}/METS.xml"
    rep_mets_rec.sha256 = sha256_file(rep_mets_rec.path)
    rep_mets_rec.rel = rep_href
    rep_file_row = mets_file_from_record(rep_mets_rec, rep_href, created)

    dmd_id = new_id("uuid")
    amd_id = new_id("uuid")
    digiprov_id = new_id("uuid")
    ingest_href = f"metadata/preservation/{ingest_path.name}"
    ingest_cs = sha256_file(ingest_path).upper()

    root_mets = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<mets xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xmlns="http://www.loc.gov/METS/"
      xmlns:csip="https://DILCIS.eu/XML/METS/CSIPExtensionMETS"
      xmlns:xlink="http://www.w3.org/1999/xlink"
      OBJID="{xml_escape.escape(package_uuid)}"
      LABEL="{title_esc}"
      TYPE="Other"
      csip:OTHERTYPE="AccessPackage"
      csip:CONTENTINFORMATIONTYPE="{cit}"
      PROFILE="{xml_escape.escape(profile_dip)}"
      xsi:schemaLocation="http://www.loc.gov/METS/ schemas/mets1_12.xsd http://www.w3.org/1999/xlink schemas/xlink.xsd https://dilcis.eu/XML/METS/CSIPExtensionMETS schemas/DILCISExtensionMETS.xsd">
    <metsHdr CREATEDATE="{created}" LASTMODDATE="{created}" RECORDSTATUS="NEW" csip:OAISPACKAGETYPE="DIP">
        <agent ROLE="CREATOR" TYPE="OTHER" OTHERTYPE="SOFTWARE">
            <name>HitSave build-dip-e-ark.py</name>
            <note csip:NOTETYPE="SOFTWARE VERSION">1.0</note>
        </agent>
    </metsHdr>
    <dmdSec ID="{dmd_id}" CREATED="{created}" STATUS="CURRENT">
        <mdRef LOCTYPE="URL" MDTYPE="OTHER" OTHERMDTYPE="DC" xlink:type="simple" xlink:href="metadata/descriptive/dc.xml" MIMETYPE="application/xml" SIZE="{dc_rec.size}" CREATED="{created}" CHECKSUM="{dc_rec.sha256.upper()}" CHECKSUMTYPE="SHA-256"/>
    </dmdSec>
    <amdSec ID="{amd_id}">
        <digiprovMD ID="{digiprov_id}" STATUS="CURRENT">
            <mdRef LOCTYPE="URL" MDTYPE="PREMIS" xlink:type="simple" xlink:href="{ingest_href}" MIMETYPE="application/xml" SIZE="{ingest_path.stat().st_size}" CREATED="{created}" CHECKSUM="{ingest_cs}" CHECKSUMTYPE="SHA-256"/>
        </digiprovMD>
    </amdSec>
    <fileSec ID="{new_id('uuid')}">
        <fileGrp ID="{schema_grp_id}" USE="Schemas"{file_grp_cit_attrs('Schemas')}>
{schema_file_rows}
        </fileGrp>
        <fileGrp ID="{doc_grp_id}" USE="Documentation"{file_grp_cit_attrs('Documentation')}>
{doc_file_row}
        </fileGrp>
        <fileGrp ID="{rep_grp_id}" USE="Representations/{rep_id}"{file_grp_cit_attrs(f'Representations/{rep_id}')}>
{rep_file_row}
        </fileGrp>
    </fileSec>
    <structMap ID="{new_id('uuid')}" TYPE="PHYSICAL" LABEL="CSIP">
        <div ID="{new_id('uuid')}" LABEL="{xml_escape.escape(package_uuid)}">
            <div ID="{new_id('uuid')}" DMDID="{dmd_id}" ADMID="{digiprov_id}" LABEL="Metadata"/>
            <div ID="{new_id('uuid')}" LABEL="Schemas">
                <fptr FILEID="{schema_grp_id}"/>
            </div>
            <div ID="{new_id('uuid')}" LABEL="Documentation">
                <fptr FILEID="{doc_grp_id}"/>
            </div>
            <div ID="{new_id('uuid')}" LABEL="Representations">
                <fptr FILEID="{rep_grp_id}"/>
                <div ID="{new_id('uuid')}" LABEL="Representations/{rep_id}">
                    <mptr xlink:type="simple" xlink:href="{rep_href}" xlink:title="{rep_grp_id}" LOCTYPE="URL"/>
                </div>
            </div>
        </div>
    </structMap>
</mets>
"""
    (package_root / "METS.xml").write_text(root_mets, encoding="utf-8")


def validate_package(package_root: Path) -> None:
    script = Path("/app/scripts/validate-e-ark-package.sh")
    if not script.is_file():
        script = VALIDATE_SCRIPT
    if not script.is_file():
        print("Warning: validate-e-ark-package.sh not found; skipping validation", file=sys.stderr)
        return
    subprocess.run(["bash", str(script), str(package_root)], check=True)


def build_dip_tar(
    source: Path,
    staging: Path,
    out_tar: Path,
    *,
    title: str,
    package_uuid: str,
    preservation: dict,
    max_files: int,
    max_bytes: int,
    video_cfg: dict,
) -> None:
    packaging = load_packaging_config()
    rep_id = packaging.get("representation_id", "rep1")
    created = iso_now()
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    data_files = collect_files(source, max_files, max_bytes)
    rep_data = staging / "representations" / rep_id / "data"
    access_dir = staging / "representations" / rep_id / "access"
    access_records: list[AccessRecord] = []
    for rec in data_files:
        dest = rep_data / rec.rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rec.path, dest)
        rec.path = dest
        if is_video_candidate(dest, video_cfg):
            group_id = f"Group-{rec.object_uuid}"
            acc = create_access_copy(dest, access_dir, group_id, video_cfg)
            if acc:
                acc.rel = rec.rel
                access_records.append(acc)

    agent_name = preservation.get("agent_name", "HitSave archive ingest")
    agent_version = preservation.get("agent_version", "build-dip-e-ark.py")
    pres_dir = staging / "metadata" / "preservation"
    pres_dir.mkdir(parents=True, exist_ok=True)
    ingest_event_id = str(uuid.uuid4())
    ingest_path = pres_dir / f"{ingest_event_id}.xml"
    ingest_path.write_text(
        premis_event_file(
            event_id=ingest_event_id,
            event_type="ingestion",
            event_time=created,
            outcome="success",
            detail=f"Built E-ARK DIP with {len(data_files)} files from {source}",
            agent_name=agent_name,
            agent_version=agent_version,
            linking_object=package_uuid,
        ),
        encoding="utf-8",
    )

    rep_mets_path = staging / "representations" / rep_id / "METS.xml"
    rep_mets_rec = write_dip_rep_mets(
        rep_mets_path,
        rep_id=rep_id,
        title=title,
        data_files=data_files,
        access_files=access_records,
        packaging=packaging,
        created=created,
    )

    schema_files = copy_schemas(staging, packaging)
    dc_path = staging / "metadata" / "descriptive" / "dc.xml"
    dc_path.parent.mkdir(parents=True, exist_ok=True)
    title_esc = xml_escape.escape(title)
    dc_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>{title_esc}</dc:title>
  <dc:identifier>{xml_escape.escape(package_uuid)}</dc:identifier>
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

    checksum_lines = ["# SHA-256 sidecar (DIP access package)", "# path\tdigest"]
    for rec in data_files:
        checksum_lines.append(f"{rec.rel}\t{rec.sha256}")
    for acc in access_records:
        checksum_lines.append(f"{acc.rel} (access)\t{acc.sha256}")
    checksum_path = staging / "metadata" / "other" / "checksums-sha256.txt"
    checksum_path.parent.mkdir(parents=True, exist_ok=True)
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    doc_path = staging / "documentation" / "README.txt"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(
        "Hit Save E-ARK DIP — access package for Omeka.\n"
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

    write_root_mets_dip(
        staging,
        package_uuid=package_uuid,
        title=title,
        rep_id=rep_id,
        rep_mets_rec=rep_mets_rec,
        schema_files=schema_files,
        doc_rec=doc_rec,
        dc_rec=dc_rec,
        ingest_path=ingest_path,
        packaging=packaging,
        created=created,
    )

    validate_package(staging)

    if out_tar.exists():
        out_tar.unlink()
    out_tar.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_tar, "w", format=tarfile.PAX_FORMAT) as tf:
        for item in staging.rglob("*"):
            if item.is_file():
                tf.add(item, arcname=item.relative_to(staging).as_posix())

    shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: build-dip-e-ark.py <game_config.yaml>")

    cfg_path = Path(sys.argv[1]).resolve()
    cfg = load_game_config(cfg_path)
    source = Path(cfg["source_game_folder"]).resolve()
    if not source.is_dir():
        raise SystemExit(f"Source not found: {source}")

    out_tar = Path(cfg["output_tar"]).resolve()
    staging = Path(cfg.get("staging_dir") or (out_tar.parent / ".staging" / out_tar.stem)).resolve()
    title = normalize_display_title(cfg.get("omeka_item_title") or source.name)
    package_uuid = cfg.get("package_uuid") or str(uuid.uuid4())
    preservation = cfg.get("preservation") or {}
    if isinstance(preservation, dict):
        preservation = {**preservation, "agent_version": "build-dip-e-ark.py"}
    max_files = int(cfg.get("max_files", 100))
    max_bytes = int(cfg.get("max_total_bytes", 524288000))
    video_cfg = load_video_access_cfg(cfg)

    build_dip_tar(
        source,
        staging,
        out_tar,
        title=title,
        package_uuid=package_uuid,
        preservation=preservation,
        max_files=max_files,
        max_bytes=max_bytes,
        video_cfg=video_cfg,
    )
    print(f"E-ARK DIP tar: {out_tar} ({out_tar.stat().st_size} bytes)")
    print(f"  OBJID: {package_uuid}")


if __name__ == "__main__":
    main()
