"""Restores from the Glacier copy: check Wasabi, request restores, write verified files back.

Shared by glacier-restore.py (command line) and glacier-restore-worker.py (ledger requests).
See docs/restore-from-glacier.md.

check_bag labels each file of a bag on Wasabi:
  ok         matches the trusted digest, and the copy has the same version
  newer      matches the trusted digest, but the copy has an older version (Wasabi changed later)
  missing    listed in the bag, but not on Wasabi
  corrupted  doesn't match the trusted digest
A missing or corrupted file is restorable only when the copy's version matches the trusted digest.

The trusted digests are Wasabi's own manifests when Wasabi's manifest-sha256.txt matches its entry
in tagmanifest-sha256.txt. Otherwise one of the two manifests is corrupted, and the copy's sha256
values are trusted instead. When Wasabi's tag manifest is the one recorded at the last copy, both
agree and the copy's values are used.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

from botocore.exceptions import ClientError

import bag_manifest
from glacier_copy_core import (
    DEFAULT_PART_SIZE,
    MANIFEST,
    TAGMANIFEST,
    WASABI,
    _hash_object,
    copy_file,
    list_source,
    list_target,
)

RESTORE_DAYS = 7

OLDER_COPY_NOTE = "The copy only has an older version of this file."
NOT_IN_COPY_NOTE = "The copy doesn't have this file."
SKIPPED_NOTE = "already correct on Wasabi, not written"
BAD = ("missing", "corrupted")


@dataclass
class FileCheck:
    bag: str  # "<wasabi bucket>/<bag prefix>/", the copy's prefix for this bag
    rel: str
    size: int
    sha256: str  # the copy's digest: what a restore would write ("" when the copy lacks the file)
    verdict: str  # ok, newer, missing, corrupted
    restorable: bool
    note: str = ""

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "FileCheck":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    @property
    def copy_key(self) -> str:
        return self.bag + self.rel

    @property
    def wasabi_key(self) -> str:
        return self.copy_key.split("/", 1)[1]


def is_ready(head: dict) -> bool:
    if head.get("StorageClass") not in ("DEEP_ARCHIVE", "GLACIER"):
        return True
    return 'ongoing-request="false"' in str(head.get("Restore") or "")


def request(dst_s3, bucket, prefix, rels, tier) -> dict:
    requested = already = 0
    for rel in rels:
        try:
            dst_s3.restore_object(Bucket=bucket, Key=prefix + rel,
                                  RestoreRequest={"Days": RESTORE_DAYS, "GlacierJobParameters": {"Tier": tier}})
            requested += 1
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "RestoreAlreadyInProgress":
                raise
            already += 1
    return {"requested": requested, "already_requested": already, "tier": tier}


def _get(s3, bucket: str, key: str) -> bytes | None:
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise


def _manifest_entries(data: bytes) -> dict[str, str] | None:
    try:
        return {rel: digest.lower() for digest, rel in bag_manifest.entries(data.decode("utf-8"))}
    except (UnicodeDecodeError, ValueError):
        return None


def _wasabi_digests(tag: bytes | None, manifest: bytes | None) -> dict[str, str] | None:
    """Digests from Wasabi's manifests, or None when they can't be trusted."""
    if tag is None or manifest is None:
        return None
    tag_entries = _manifest_entries(tag)
    payload = _manifest_entries(manifest)
    if tag_entries is None or payload is None:
        return None
    if tag_entries.get(MANIFEST) != hashlib.sha256(manifest).hexdigest():
        return None
    return {**payload, **tag_entries, TAGMANIFEST: hashlib.sha256(tag).hexdigest()}


def check_bag(wasabi_s3, wasabi_bucket: str, bag_prefix: str, aws_s3, aws_bucket: str,
              recorded_tagmanifest_sha256: str) -> list[FileCheck]:
    bag_prefix = bag_prefix.strip("/")
    w_root = bag_prefix + "/"
    c_root = f"{wasabi_bucket}/{w_root}"
    copy = list_target(aws_s3, aws_bucket, c_root)
    on_wasabi = list_source(wasabi_s3, wasabi_bucket, w_root)
    copy_digests = {rel: t.sha256 for rel, t in copy.items() if t.sha256}

    tag = _get(wasabi_s3, wasabi_bucket, w_root + TAGMANIFEST)
    if tag is not None and hashlib.sha256(tag).hexdigest() == recorded_tagmanifest_sha256:
        trusted = copy_digests
    else:
        manifest = _get(wasabi_s3, wasabi_bucket, w_root + MANIFEST)
        trusted = _wasabi_digests(tag, manifest) or copy_digests

    files: list[FileCheck] = []
    for rel in sorted(trusted):
        want = trusted[rel]
        in_copy = copy.get(rel)
        copy_sha = (in_copy.sha256 or "") if in_copy else ""
        size = in_copy.size if in_copy else on_wasabi.get(rel, 0)
        if rel not in on_wasabi:
            verdict = "missing"
        elif _hash_object(wasabi_s3, wasabi_bucket, w_root + rel, DEFAULT_PART_SIZE) == want:
            verdict = "ok" if copy_sha == want else "newer"
        else:
            verdict = "corrupted"
        restorable = verdict in BAD and copy_sha == want
        note = ""
        if verdict in BAD and not restorable:
            note = OLDER_COPY_NOTE if in_copy else NOT_IN_COPY_NOTE
        files.append(FileCheck(c_root, rel, size, copy_sha, verdict, restorable, note))
    return files


def _files(n: int) -> str:
    return "file" if n == 1 else "files"


def summary_message(files: list[FileCheck]) -> tuple[str, str]:
    """(next state, message) after a check: not_needed, cannot_restore or restoring."""
    bad = [f for f in files if f.verdict in BAD]
    fixable = [f for f in bad if f.restorable]
    stuck = len(bad) - len(fixable)
    fine = len(files) - len(bad)
    if not bad:
        return "not_needed", "No file on Wasabi is missing or corrupted. Nothing needs restoring."
    if not fixable:
        if stuck == 1:
            text = "1 file is missing or corrupted, but the copy only has an older version of it."
        else:
            text = f"{stuck} files are missing or corrupted, but the copy only has older versions of them."
        return "cannot_restore", text + " See docs/restore-from-glacier.md."
    corrupted = sum(f.verdict == "corrupted" for f in fixable)
    missing = len(fixable) - corrupted
    counts = [f"{n} {label}" for n, label in ((corrupted, "corrupted"), (missing, "missing")) if n]
    last = missing or corrupted
    parts = [f"Restoring {' and '.join(counts)} {_files(last)}."]
    if fine:
        verb = "is" if fine == 1 else "are"
        parts.append(f"{fine} {_files(fine)} on Wasabi {verb} fine and won't be restored.")
    if stuck:
        tail = "an older version of it" if stuck == 1 else "older versions of them"
        parts.append(f"{stuck} more {_files(stuck)} can't be restored, because the copy only has {tail}.")
    return "restoring", " ".join(parts)


def restore_progress(aws_s3, aws_bucket: str, files: list[FileCheck]) -> tuple[int, int, datetime | None]:
    """(ready, waiting, earliest time a restored file stops being readable)."""
    ready = waiting = 0
    expiry: datetime | None = None
    for f in files:
        head = aws_s3.head_object(Bucket=aws_bucket, Key=f.copy_key)
        if not is_ready(head):
            waiting += 1
            continue
        ready += 1
        restore = str(head.get("Restore") or "")
        if 'expiry-date="' in restore:
            when = parsedate_to_datetime(restore.split('expiry-date="', 1)[1].split('"', 1)[0])
            expiry = when if expiry is None else min(expiry, when)
    return ready, waiting, expiry


def verify_restored(aws_s3, aws_bucket: str, files: list[FileCheck]) -> list[str]:
    """Read each restored file and compare its SHA-256 with the copy's recorded digest."""
    problems = []
    for f in files:
        if _hash_object(aws_s3, aws_bucket, f.copy_key, DEFAULT_PART_SIZE) != f.sha256:
            problems.append(f"{f.rel}: the restored file doesn't match its SHA-256")
    return problems


def write_back(aws_s3, aws_bucket: str, wasabi_s3, wasabi_bucket: str, f: FileCheck) -> tuple[str, str]:
    """Put one restored file back on Wasabi: ("yes" | "skipped" | "no", note).

    Skips the file when Wasabi already has the right bytes. Otherwise the upload completes only
    when the bytes read from the copy match f.sha256, so wrong bytes never replace a Wasabi file.
    """
    try:
        current = _hash_object(wasabi_s3, wasabi_bucket, f.wasabi_key, DEFAULT_PART_SIZE)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
            raise
        current = None
    if current == f.sha256:
        return "skipped", SKIPPED_NOTE
    result = copy_file(aws_s3, aws_bucket, f.copy_key, wasabi_s3, wasabi_bucket, f.wasabi_key, f.sha256,
                       label=f.rel, size=f.size, target=WASABI)
    return ("yes", "") if result.ok else ("no", result.problem)
