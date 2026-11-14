"""Copy one BagIt package from Wasabi to S3 Glacier Deep Archive, checking every file on the way.

Each Wasabi file becomes one S3 object at <wasabi bucket>/<key>. While a file streams through,
its SHA-256 is compared with the bag's manifest-sha256.txt or tagmanifest-sha256.txt, and a
full-object CRC64NVME goes with the upload so S3 rejects bytes that changed in transit. The
SHA-256 is stored as x-amz-meta-sha256, which a HEAD request returns without a restore.

A damaged file (SHA-256 differs from the manifest) is never uploaded. See docs/glacier-copy.md.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from botocore.exceptions import ClientError
from botocore.httpchecksum import CrtCrc64NvmeChecksum

import bag_manifest

DEFAULT_PART_SIZE = 64 * 2**20
STORAGE_CLASS = "DEEP_ARCHIVE"
MANIFEST = "manifest-sha256.txt"
TAGMANIFEST = "tagmanifest-sha256.txt"
LOCATION = "aws-deep-archive"
AWS_CONFIG = Path("/config/preservation/aws-copy.yaml")
INGEST_CONFIG = Path("/config/preservation/ingest.yaml")
FATAL_CODES = {
    "AccessDenied", "InvalidAccessKeyId", "ExpiredToken", "SignatureDoesNotMatch",
    "InvalidToken", "TokenRefreshRequired", "AllAccessDisabled", "AccountProblem",
}


class FatalError(RuntimeError):
    """Stops the run: credentials or permissions are wrong, so no package result can be trusted."""


def is_fatal(exc: BaseException) -> bool:
    return isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in FATAL_CODES


@dataclass(frozen=True)
class TargetObject:
    size: int
    sha256: str | None


@dataclass
class PackagePlan:
    upload: list[str] = field(default_factory=list)
    delete_marker: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


@dataclass
class CopyResult:
    ok: bool
    sha256: str = ""
    crc64nvme: str = ""
    bytes: int = 0
    problem: str = ""


@dataclass
class PackageResult:
    files_uploaded: int = 0
    files_unchanged: int = 0
    delete_markers: int = 0
    bytes_read: int = 0
    object_count: int = 0
    total_bytes: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def plan_package(source: dict[str, int], target: dict[str, TargetObject], expected: dict[str, str]) -> PackagePlan:
    """Decide per file: upload, leave alone, or add a delete marker.

    A file is unchanged only when the copy has the same size and its sha256 metadata equals the
    digest the bag expects. A file with no expected digest is always uploaded.
    """
    plan = PackagePlan()
    for rel in sorted(source):
        t = target.get(rel)
        want = expected.get(rel)
        if t is not None and want and t.size == source[rel] and t.sha256 == want:
            plan.unchanged.append(rel)
        else:
            plan.upload.append(rel)
    plan.delete_marker = sorted(rel for rel in target if rel not in source)
    return plan


def _read_exactly(body, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = body.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _hash_object(src_s3, bucket: str, key: str, part_size: int) -> str:
    sha = hashlib.sha256()
    body = src_s3.get_object(Bucket=bucket, Key=key)["Body"]
    while chunk := _read_exactly(body, part_size):
        sha.update(chunk)
    return sha.hexdigest()


def copy_file(src_s3, src_bucket: str, key: str, dst_s3, dst_bucket: str, dst_key: str,
              expected_sha256: str | None, part_size: int = DEFAULT_PART_SIZE, label: str | None = None) -> CopyResult:
    """Stream one file from Wasabi into Deep Archive.

    Returns ok=False, with nothing stored, when the SHA-256 differs from expected_sha256.
    Any other error aborts a started multipart upload and propagates.

    S3 takes an object's metadata when the upload starts, so x-amz-meta-sha256 is the digest the
    bag expects, and the upload completes only if the streamed bytes match it. A file larger than
    one part with no expected digest is read twice: once to hash it, once to upload it.
    """
    label = label or key
    body = src_s3.get_object(Bucket=src_bucket, Key=key)["Body"]
    sha = hashlib.sha256()
    crc = CrtCrc64NvmeChecksum()
    size = 0

    def take() -> bytes:
        nonlocal size
        chunk = _read_exactly(body, part_size)
        sha.update(chunk)
        crc.update(chunk)
        size += len(chunk)
        return chunk

    def mismatch() -> CopyResult:
        return CopyResult(False, sha.hexdigest(), crc.b64digest(), size,
                          f"SHA-256 of {label} differs from the bag manifest")

    want = expected_sha256.lower() if expected_sha256 else None
    first = take()
    second = take() if len(first) == part_size else b""

    if not second:
        if want and sha.hexdigest() != want:
            return mismatch()
        digest = sha.hexdigest()
        dst_s3.put_object(Bucket=dst_bucket, Key=dst_key, Body=first, StorageClass=STORAGE_CLASS,
                          Metadata={"sha256": digest}, ChecksumAlgorithm="CRC64NVME",
                          ChecksumCRC64NVME=crc.b64digest())
        return _check_stored(dst_s3, dst_bucket, dst_key, CopyResult(True, digest, crc.b64digest(), size))

    if want is None:
        want = _hash_object(src_s3, src_bucket, key, part_size)
    upload_id = dst_s3.create_multipart_upload(
        Bucket=dst_bucket, Key=dst_key, StorageClass=STORAGE_CLASS, Metadata={"sha256": want},
        ChecksumAlgorithm="CRC64NVME", ChecksumType="FULL_OBJECT",
    )["UploadId"]
    try:
        parts = []
        chunk = first
        while chunk:
            number = len(parts) + 1
            resp = dst_s3.upload_part(Bucket=dst_bucket, Key=dst_key, UploadId=upload_id, PartNumber=number,
                                      Body=chunk, ChecksumAlgorithm="CRC64NVME")
            parts.append({"PartNumber": number, "ETag": resp["ETag"],
                          "ChecksumCRC64NVME": resp["ChecksumCRC64NVME"]})
            if second:
                chunk, second = second, b""
            else:
                chunk = take()
        if sha.hexdigest() != want:
            dst_s3.abort_multipart_upload(Bucket=dst_bucket, Key=dst_key, UploadId=upload_id)
            return mismatch()
        dst_s3.complete_multipart_upload(
            Bucket=dst_bucket, Key=dst_key, UploadId=upload_id, MultipartUpload={"Parts": parts},
            ChecksumCRC64NVME=crc.b64digest(), ChecksumType="FULL_OBJECT",
        )
    except BaseException:
        try:
            dst_s3.abort_multipart_upload(Bucket=dst_bucket, Key=dst_key, UploadId=upload_id)
        except Exception:  # noqa: BLE001 - the bucket's lifecycle rule removes it after 7 days
            pass
        raise
    return _check_stored(dst_s3, dst_bucket, dst_key, CopyResult(True, want, crc.b64digest(), size))


def _check_stored(dst_s3, bucket: str, key: str, result: CopyResult) -> CopyResult:
    """HEAD the new object: size, CRC64NVME and sha256 metadata must match what was sent."""
    head = dst_s3.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
    if int(head["ContentLength"]) != result.bytes or head.get("ChecksumCRC64NVME") != result.crc64nvme:
        raise RuntimeError(f"stored object differs from what was sent: s3://{bucket}/{key}")
    if (head.get("Metadata") or {}).get("sha256") != result.sha256:
        raise RuntimeError(f"stored object lacks its sha256 metadata: s3://{bucket}/{key}")
    return result


def expected_digests(src_s3, bucket: str, prefix: str) -> tuple[dict[str, str], bytes]:
    """{relative path: sha256} from the bag's two manifests, plus the tag manifest's own digest.

    Returns the tag manifest bytes too, so the caller can tell whether the bag changed later.
    """
    root = prefix.strip("/") + "/"
    tag = src_s3.get_object(Bucket=bucket, Key=root + TAGMANIFEST)["Body"].read()
    payload = src_s3.get_object(Bucket=bucket, Key=root + MANIFEST)["Body"].read()
    expected: dict[str, str] = {}
    for text in (payload.decode("utf-8"), tag.decode("utf-8")):
        for digest, rel in bag_manifest.entries(text):
            expected[rel] = digest.lower()
    expected[TAGMANIFEST] = hashlib.sha256(tag).hexdigest()
    return expected, tag


def list_source(src_s3, bucket: str, root: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for page in src_s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=root):
        for o in page.get("Contents") or []:
            key = str(o["Key"])
            if not key.endswith("/"):
                out[key[len(root):]] = int(o["Size"])
    return out


def list_target(dst_s3, bucket: str, root: str) -> dict[str, TargetObject]:
    """Current objects under root, with their sha256 metadata (one HEAD each, no restore)."""
    out: dict[str, TargetObject] = {}
    for page in dst_s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=root):
        for o in page.get("Contents") or []:
            key = str(o["Key"])
            head = dst_s3.head_object(Bucket=bucket, Key=key)
            out[key[len(root):]] = TargetObject(int(head["ContentLength"]), (head.get("Metadata") or {}).get("sha256"))
    return out


def copy_package(src_s3, src_bucket: str, prefix: str, dst_s3, dst_bucket: str,
                 part_size: int = DEFAULT_PART_SIZE) -> PackageResult:
    """Make the copy of one package match Wasabi: upload new and changed files, add delete markers
    for files gone from Wasabi. Damaged files are listed in problems and not uploaded."""
    src_root = prefix.strip("/") + "/"
    dst_root = f"{src_bucket}/{src_root}"
    expected, _ = expected_digests(src_s3, src_bucket, prefix)
    source = list_source(src_s3, src_bucket, src_root)
    plan = plan_package(source, list_target(dst_s3, dst_bucket, dst_root), expected)
    result = PackageResult(files_unchanged=len(plan.unchanged), object_count=len(source),
                           total_bytes=sum(source.values()))
    for rel in plan.upload:
        r = copy_file(src_s3, src_bucket, src_root + rel, dst_s3, dst_bucket, dst_root + rel,
                      expected.get(rel), part_size=part_size, label=rel)
        result.bytes_read += r.bytes
        if r.ok:
            result.files_uploaded += 1
        else:
            result.problems.append(r.problem)
    # A file still in the bag's manifests but not on Wasabi is lost, not deleted: report it and
    # leave the copy's version current.
    for rel in sorted(r for r in expected if r not in source):
        result.problems.append(f"{rel} is in the bag manifest but missing on Wasabi")
    for rel in plan.delete_marker:
        if rel in expected:
            continue
        dst_s3.delete_object(Bucket=dst_bucket, Key=dst_root + rel)
        result.delete_markers += 1
    return result


# Shared by glacier-copy.py, glacier-copy-check.py and glacier-restore.py.

def prefixes_of(row: dict) -> list[str]:
    out = [str(row["package_storage_prefix"]).strip("/")]
    if row.get("disc_contents_prefix"):
        out.append(str(row["disc_contents_prefix"]).strip("/"))
    return out


def fingerprint(src_s3, bucket: str, prefixes: list[str]) -> str:
    """SHA-256 of each bag's tag manifest, comma-separated (package first, then disc contents)."""
    digests = []
    for prefix in prefixes:
        tag = src_s3.get_object(Bucket=bucket, Key=f"{prefix}/{TAGMANIFEST}")["Body"].read()
        digests.append(hashlib.sha256(tag).hexdigest())
    return ",".join(digests)


def make_aws_client(aws: dict, creds: dict):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=str(aws["region"]),
        aws_access_key_id=str(creds["access_key_id"]),
        aws_secret_access_key=str(creds["secret_access_key"]),
        config=Config(retries={"max_attempts": 10, "mode": "adaptive"}),
    )


def connect():
    """(ledger connection, Wasabi client, AWS client, AWS settings, bucket_for) from /config."""
    import psycopg

    from preservation_common import wasabi_bucket_for_storage_prefix
    from upload_bag_wasabi import load_wasabi_settings, make_s3_client, resolve_config_path
    from yaml_config import load_yaml

    ingest = load_yaml(INGEST_CONFIG)
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    wasabi, _ = load_wasabi_settings(INGEST_CONFIG)
    config_root = INGEST_CONFIG.parent.parent
    src = make_s3_client(wasabi, load_yaml(resolve_config_path(str(wasabi["credentials_file"]), config_root)))
    aws = load_yaml(AWS_CONFIG)
    creds = load_yaml(resolve_config_path(str(aws["credentials_file"]), config_root))
    dst = make_aws_client(aws, creds)
    conn = psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                           user=db["user"], password=db["password"])
    return conn, src, dst, aws, lambda kind, prefix: wasabi_bucket_for_storage_prefix(wasabi, prefix, ingest_kind=kind)


