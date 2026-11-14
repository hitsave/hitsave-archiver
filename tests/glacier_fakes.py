"""In-memory S3 for the Glacier copy tests: versions, multipart uploads, CRC64NVME, restores."""
from __future__ import annotations

import base64
import hashlib
import io
import itertools
from dataclasses import dataclass, field

from botocore.exceptions import ClientError
from botocore.httpchecksum import CrtCrc64NvmeChecksum


def crc64nvme_b64(data: bytes) -> str:
    c = CrtCrc64NvmeChecksum()
    c.update(data)
    return c.b64digest()


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def client_error(code: str, op: str, status: int = 400) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, op)


@dataclass
class Version:
    data: bytes | None  # None = delete marker
    metadata: dict = field(default_factory=dict)
    storage_class: str = "STANDARD"
    crc: str | None = None
    version_id: str = ""
    restored: bool = False
    restore_requests: list = field(default_factory=list)


class FakeS3:
    _ids = itertools.count(1)

    def __init__(self) -> None:
        self.versions: dict[str, list[Version]] = {}
        self.uploads: dict[str, dict] = {}
        self.completed: list[dict] = []
        self.aborted: list[str] = []
        self.puts: list[str] = []
        self.restores: list[dict] = []
        self.denied = False
        self.on_get = None  # callback(key) run before each get_object

    # helpers for tests
    def put(self, key: str, data: bytes, storage_class: str = "STANDARD", metadata: dict | None = None) -> None:
        self._store(key, Version(data, dict(metadata or {}), storage_class, crc64nvme_b64(data)))

    @property
    def objects(self) -> dict[str, Version]:
        return {k: v[-1] for k, v in self.versions.items() if v and v[-1].data is not None}

    def deny_all(self) -> None:
        self.denied = True

    def _check(self, op: str) -> None:
        if self.denied:
            raise client_error("AccessDenied", op, 403)

    def _store(self, key: str, v: Version) -> None:
        v.version_id = f"v{next(self._ids)}"
        self.versions.setdefault(key, []).append(v)

    def _current(self, key: str, op: str) -> Version:
        cur = self.objects.get(key)
        if cur is None:
            raise client_error("NoSuchKey" if op != "HeadObject" else "404", op, 404)
        return cur

    # S3 API
    def put_object(self, Bucket, Key, Body, Metadata=None, StorageClass="STANDARD", ChecksumCRC64NVME=None, **_):
        self._check("PutObject")
        data = Body if isinstance(Body, bytes) else Body.read()
        if ChecksumCRC64NVME is not None and ChecksumCRC64NVME != crc64nvme_b64(data):
            raise client_error("BadDigest", "PutObject")
        self.puts.append(Key)
        self._store(Key, Version(data, dict(Metadata or {}), StorageClass, crc64nvme_b64(data)))
        return {"VersionId": self.versions[Key][-1].version_id}

    def create_multipart_upload(self, Bucket, Key, Metadata=None, StorageClass="STANDARD", **kw):
        self._check("CreateMultipartUpload")
        uid = f"u{next(self._ids)}"
        self.uploads[uid] = {"key": Key, "parts": {}, "metadata": dict(Metadata or {}),
                             "storage_class": StorageClass, "kwargs": kw}
        return {"UploadId": uid}

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body, **_):
        self._check("UploadPart")
        data = Body if isinstance(Body, bytes) else Body.read()
        self.uploads[UploadId]["parts"][PartNumber] = data
        return {"ETag": f'"{hashlib.md5(data).hexdigest()}"', "ChecksumCRC64NVME": crc64nvme_b64(data)}

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload, ChecksumCRC64NVME=None,
                                  ChecksumType=None, **_):
        self._check("CompleteMultipartUpload")
        up = self.uploads.pop(UploadId)
        numbers = [p["PartNumber"] for p in MultipartUpload["Parts"]]
        data = b"".join(up["parts"][n] for n in numbers)
        if ChecksumCRC64NVME is not None and ChecksumCRC64NVME != crc64nvme_b64(data):
            raise client_error("BadDigest", "CompleteMultipartUpload")
        self.completed.append({"Key": Key, "ChecksumCRC64NVME": ChecksumCRC64NVME, "ChecksumType": ChecksumType,
                               "Parts": numbers, **up["kwargs"]})
        self.puts.append(Key)
        self._store(Key, Version(data, up["metadata"], up["storage_class"], crc64nvme_b64(data)))
        return {}

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.uploads.pop(UploadId, None)
        self.aborted.append(Key)
        return {}

    def head_object(self, Bucket, Key, ChecksumMode=None, **_):
        self._check("HeadObject")
        v = self._current(Key, "HeadObject")
        out = {"ContentLength": len(v.data), "Metadata": dict(v.metadata), "StorageClass": v.storage_class,
               "VersionId": v.version_id}
        if ChecksumMode == "ENABLED":
            out["ChecksumCRC64NVME"] = v.crc
            out["ChecksumType"] = "FULL_OBJECT"
        if v.restored:
            out["Restore"] = 'ongoing-request="false", expiry-date="Fri, 27 Nov 2026 00:00:00 GMT"'
        elif v.restore_requests:
            out["Restore"] = 'ongoing-request="true"'
        return out

    def get_object(self, Bucket, Key, **_):
        self._check("GetObject")
        if self.on_get:
            self.on_get(Key)
        v = self._current(Key, "GetObject")
        if v.storage_class in ("DEEP_ARCHIVE", "GLACIER") and not v.restored:
            raise client_error("InvalidObjectState", "GetObject", 403)
        return {"Body": io.BytesIO(v.data), "ContentLength": len(v.data)}

    def delete_object(self, Bucket, Key, **_):
        self._check("DeleteObject")
        self._store(Key, Version(None))
        return {"DeleteMarker": True}

    def restore_object(self, Bucket, Key, RestoreRequest, **_):
        self._check("RestoreObject")
        v = self._current(Key, "RestoreObject")
        v.restore_requests.append(RestoreRequest)
        self.restores.append({"Key": Key, **RestoreRequest})
        return {}

    def get_paginator(self, name):
        s3 = self

        class Pager:
            def paginate(self, Bucket, Prefix=""):
                s3._check(name)
                if name == "list_objects_v2":
                    yield {"Contents": [{"Key": k, "Size": len(v.data)} for k, v in sorted(s3.objects.items())
                                        if k.startswith(Prefix)]}
                elif name == "list_object_versions":
                    versions, markers = [], []
                    for k, vs in sorted(s3.versions.items()):
                        if not k.startswith(Prefix):
                            continue
                        for i, v in enumerate(vs):
                            row = {"Key": k, "VersionId": v.version_id, "IsLatest": i == len(vs) - 1}
                            if v.data is None:
                                markers.append(row)
                            else:
                                versions.append({**row, "Size": len(v.data), "StorageClass": v.storage_class})
                    yield {"Versions": versions, "DeleteMarkers": markers}
                else:
                    raise NotImplementedError(name)

        return Pager()


def b64_to_hex(value: str) -> str:
    return base64.b64decode(value).hex()
