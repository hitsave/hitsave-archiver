#!/usr/bin/env python3
"""Check METS @PROFILE config: HTTP where published; AIP normative URI vs document mirror."""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
HTTP_PROFILE_KEYS = ("mets_profile_csip", "mets_profile_dip")
AIP_IDENTIFIER_KEY = "mets_profile_aip"
AIP_DOCUMENT_KEY = "mets_profile_aip_document"
NORMATIVE_URI_IN_PROFILE = re.compile(
    rb"<URI[^>]*>\s*(https://[^\s<]+/profile/E-ARK-AIP-v2-2-0\.xml)\s*</URI>"
)


def load_preservation() -> dict:
    for path in (
        Path("/config/preservation/packaging.yaml"),
        ROOT / "config" / "preservation" / "packaging.yaml",
    ):
        if path.is_file():
            data = yaml.safe_load(path.read_text()) or {}
            return data.get("preservation") or {}
    raise FileNotFoundError("packaging.yaml not found")


def fetch(url: str, timeout: float = 20.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        return 0, str(exc.reason).encode()


def check_http_200(key: str, url: str) -> bool:
    status, _ = fetch(url)
    if status == 200:
        print(f"OK   {key}: {url}")
        return True
    print(f"FAIL {key}: HTTP {status} for {url}", file=sys.stderr)
    return False


def check_aip_profiles(pres: dict) -> bool:
    """AIP @PROFILE string is normative (earkdip); document is served elsewhere (earkaip)."""
    identifier = str(pres.get(AIP_IDENTIFIER_KEY) or "").strip()
    document = str(pres.get(AIP_DOCUMENT_KEY) or "").strip()
    if not identifier:
        print(f"FAIL {AIP_IDENTIFIER_KEY}: not set", file=sys.stderr)
        return False

    id_status, _ = fetch(identifier)
    if id_status == 200:
        print(f"OK   {AIP_IDENTIFIER_KEY} (HTTP 200): {identifier}")
    else:
        print(
            f"WARN {AIP_IDENTIFIER_KEY}: HTTP {id_status} for {identifier} "
            "(expected for DILCIS AIP v2.2.0 — AIPM2 uses this URI as METS @PROFILE, "
            "not as a download URL)",
        )

    if not document:
        print(f"FAIL {AIP_DOCUMENT_KEY}: not set (need fetchable profile document)", file=sys.stderr)
        return False
    doc_status, body = fetch(document)
    if doc_status != 200:
        print(f"FAIL {AIP_DOCUMENT_KEY}: HTTP {doc_status} for {document}", file=sys.stderr)
        return False
    print(f"OK   {AIP_DOCUMENT_KEY}: {document}")

    match = NORMATIVE_URI_IN_PROFILE.search(body)
    if not match:
        print(f"FAIL {AIP_DOCUMENT_KEY}: no normative <URI> in profile XML", file=sys.stderr)
        return False
    normative = match.group(1).decode("utf-8")
    if normative != identifier:
        print(
            f"FAIL {AIP_IDENTIFIER_KEY}: packaging has {identifier!r} but profile document "
            f"declares normative URI {normative!r}",
            file=sys.stderr,
        )
        return False
    print(f"OK   AIP identifier matches profile document <URI>: {identifier}")
    return True


def main() -> int:
    pres = load_preservation()
    ok = check_aip_profiles(pres)
    for key in HTTP_PROFILE_KEYS:
        url = pres.get(key)
        if not url:
            print(f"SKIP {key}: not set", file=sys.stderr)
            continue
        ok = check_http_200(key, str(url)) and ok
    if not ok:
        print(
            "See docs/preservation-packaging-standard.md §8.0 (AIP earkdip vs earkaip).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
