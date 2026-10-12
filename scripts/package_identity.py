"""Press BagIt package ids: title slug + UUID v7, sip_fingerprint dedup, Wasabi sort letters."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from uuid6 import uuid7
except ImportError:
    uuid7 = None  # type: ignore[misc, assignment]

_PACKAGE_UUID_SUFFIX = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


def title_slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return (slug[:100] or "game").strip("-")


def sort_letter(catalog_title: str) -> str:
    for ch in (catalog_title or "").strip():
        if ch.isalpha():
            return ch.upper()
        if ch.isdigit():
            return "0-9"
    return "X"


def relative_sip_path(source: Path, press_material_root: Path) -> str:
    try:
        return source.resolve().relative_to(press_material_root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"SIP {source} is not under press root {press_material_root}") from exc


def canonical_sip_line(submission_source_id: str, relative_path: str) -> str:
    sid = submission_source_id.strip()
    rel = relative_path.strip().strip("/")
    if not sid or not rel:
        raise ValueError("submission_source_id and relative SIP path required")
    return f"{sid}|{rel}"


def sip_fingerprint_sha256(submission_source_id: str, relative_path: str) -> str:
    digest = hashlib.sha256(canonical_sip_line(submission_source_id, relative_path).encode("utf-8"))
    return digest.hexdigest()


def mint_uuid7() -> str:
    if uuid7 is None:
        raise RuntimeError("uuid6 package required for UUID v7 (pip install uuid6)")
    return str(uuid7())


def make_package_id(catalog_title: str, package_uuid: str | None = None) -> tuple[str, str]:
    slug = title_slug(catalog_title)
    uid = package_uuid or mint_uuid7()
    return f"{slug}-{uid}", uid


def parse_package_id(package_id: str) -> tuple[str, str]:
    match = _PACKAGE_UUID_SUFFIX.search(package_id.strip())
    if not match:
        raise ValueError(f"package_id has no UUID v7 suffix: {package_id!r}")
    uid = match.group(1).lower()
    slug = package_id[: match.start()].rstrip("-")
    return slug, uid


@dataclass(frozen=True)
class PackageIdentity:
    title_slug: str
    sort_letter: str
    package_id: str
    package_uuid: str
    sip_fingerprint: str
    relative_sip_path: str
    submission_source_id: str
    omeka_item_title: str

    def wasabi_object_basename(self) -> str:
        return f"{self.package_id}.tar"

    def external_identifier(self) -> str:
        return f"hitsave:package:{self.package_uuid}"


def build_package_identity(
    *,
    source: Path,
    press_material_root: Path,
    submission_source_id: str,
    omeka_item_title: str,
    existing_package_id: str | None = None,
    existing_package_uuid: str | None = None,
) -> PackageIdentity:
    rel = relative_sip_path(source, press_material_root)
    fp = sip_fingerprint_sha256(submission_source_id, rel)
    letter = sort_letter(omeka_item_title)
    if existing_package_id:
        slug, uid = parse_package_id(existing_package_id)
        if existing_package_uuid:
            uid = existing_package_uuid
        pid = existing_package_id
    else:
        pid, uid = make_package_id(omeka_item_title, existing_package_uuid)
        slug = title_slug(omeka_item_title)
    return PackageIdentity(
        title_slug=slug,
        sort_letter=letter,
        package_id=pid,
        package_uuid=uid,
        sip_fingerprint=fp,
        relative_sip_path=rel,
        submission_source_id=submission_source_id,
        omeka_item_title=omeka_item_title,
    )
