"""Wasabi S3 extension: rename/move objects by key (HTTP MOVE).

See https://docs.wasabi.com/apidocs/operations-on-objects (Renaming Objects).
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
from typing import Any
from urllib.parse import quote

from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError


def _encode_key(key: str) -> str:
    """Percent-encode an S3 key once (UTF-8), keeping '/' and '~' literal."""
    return quote(key, safe="/~")


def _send_move(s3_client, *, bucket: str, source: str, headers: dict[str, str]):
    """Send a signed Wasabi MOVE for ``source``; return (status, body bytes).

    The URL path is percent-encoded exactly once and signed as-is (S3SigV4Auth does not
    re-normalize it), so keys with spaces, apostrophes or non-ASCII characters sign correctly.
    """
    endpoint = str(s3_client.meta.endpoint_url).rstrip("/")
    url = f"{endpoint}/{bucket}/{_encode_key(source)}"
    request = AWSRequest(
        method="MOVE",
        url=url,
        headers={**headers, "X-Amz-Content-Sha256": "UNSIGNED-PAYLOAD"},
        data=b"",
    )
    credentials = s3_client._request_signer._credentials  # noqa: SLF001
    S3SigV4Auth(credentials, "s3", s3_client.meta.region_name).add_auth(request)
    http = s3_client._endpoint.http_session  # noqa: SLF001
    response = http.send(request.prepare())
    status = getattr(response, "status_code", None) or response.raw.status
    return int(status), response.content


def _raise_for_status(status: int, body: bytes, operation: str) -> None:
    if status >= 400:
        code = str(status)
        if b"<Code>NoSuchKey</Code>" in body:
            code = "NoSuchKey"
        raise ClientError(
            {"Error": {"Code": code, "Message": body.decode("utf-8", errors="replace")[:500]}},
            operation,
        )


def _list_object_keys(s3_client, *, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    for page in s3_client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys += [str(o["Key"]) for o in page.get("Contents") or []]
    return keys


def wasabi_move_prefix(
    s3_client,
    *,
    bucket: str,
    source_prefix: str,
    destination_prefix: str,
    overwrite: bool = False,
    quiet: bool = True,
) -> dict[str, Any]:
    """
    Rename all objects whose keys start with source_prefix to destination_prefix.

    Uses Wasabi MOVE with X-Wasabi-Prefix: true (folder-style rename). Wasabi only accepts a
    prefix MOVE when a zero-byte folder placeholder ("prefix/") exists; otherwise it answers
    404 NoSuchKey. In that case each object is moved individually instead (same result shape).
    """
    src = source_prefix.strip("/") + "/"
    dest = destination_prefix.strip("/") + "/"
    if src == dest:
        return {"move_count": 0, "skipped": True, "errors": []}

    headers = {
        # Percent-encoded: header values can't carry non-ASCII; Wasabi decodes it (verified live).
        "Destination": _encode_key(dest),
        "X-Wasabi-Prefix": "true",
        "X-Wasabi-Quiet": "true" if quiet else "false",
        "Overwrite": "true" if overwrite else "false",
    }
    status, body = _send_move(s3_client, bucket=bucket, source=src, headers=headers)
    if status == 404 and b"<Code>NoSuchKey</Code>" in body:
        return _move_prefix_per_key(
            s3_client, bucket=bucket, src=src, dest=dest, overwrite=overwrite
        )
    _raise_for_status(status, body, "WasabiMove")
    return _parse_move_result(body)


def _move_prefix_per_key(
    s3_client, *, bucket: str, src: str, dest: str, overwrite: bool
) -> dict[str, Any]:
    """Fallback for prefixes without a folder placeholder: MOVE each object on its own."""
    objects: list[dict[str, str]] = []
    errors: list[str] = []
    for key in _list_object_keys(s3_client, bucket=bucket, prefix=src):
        dest_key = dest + key[len(src):]
        err = ""
        try:
            wasabi_move_key(
                s3_client, bucket=bucket, source_key=key, destination_key=dest_key, overwrite=overwrite
            )
        except ClientError as e:
            err = str(e)[:300]
            errors.append(f"{key}: {err}")
        objects.append({"source_key": key, "destination_key": dest_key, "error": err})
    if not objects:
        raise ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": f"no objects under {src}"}}, "WasabiMove"
        )
    return {
        "move_count": sum(1 for o in objects if not o["error"]),
        "source_prefix": src,
        "destination_prefix": dest,
        "objects": objects,
        "errors": errors,
        "per_key_fallback": True,
    }


def wasabi_move_key(
    s3_client,
    *,
    bucket: str,
    source_key: str,
    destination_key: str,
    overwrite: bool = False,
) -> None:
    """Rename one object (Wasabi MOVE without X-Wasabi-Prefix)."""
    src = source_key.lstrip("/")
    dest = destination_key.lstrip("/")
    if src == dest:
        return
    headers = {
        "Destination": _encode_key(dest),
        "X-Wasabi-Quiet": "true",
        "Overwrite": "true" if overwrite else "false",
    }
    status, body = _send_move(s3_client, bucket=bucket, source=src, headers=headers)
    _raise_for_status(status, body, "WasabiMoveKey")


# Wasabi's MoveObjectResult does not reliably XML-escape key text ('&', '<', '>' come back raw,
# while "'" comes back as &apos;). Re-escape the text of every key-bearing element before parsing.
_KEY_ELEMENT = re.compile(
    r"<(SourceKey|DestinationKey|SourcePrefix|DestinationPrefix|Key|Error)>(.*?)</\1>", re.S
)


def _escape_key_text(xml_text: str) -> str:
    def fix(m: re.Match) -> str:
        raw = html.unescape(m.group(2))
        return f"<{m.group(1)}>{xml_escape(raw)}</{m.group(1)}>"

    return _KEY_ELEMENT.sub(fix, xml_text)


def _parse_move_result(body: bytes) -> dict[str, Any]:
    if not body.strip():
        return {"move_count": 0, "errors": [], "objects": []}
    text = _escape_key_text(body.decode("utf-8", errors="replace"))
    root = ET.fromstring(text.encode("utf-8"))
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if tag != "MoveObjectResult":
        return {"move_count": 0, "errors": [f"unexpected XML root: {root.tag}"], "objects": []}

    def local(tag_name: str) -> str:
        for el in root.iter():
            name = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if name == tag_name:
                return (el.text or "").strip()
        return ""

    move_count_raw = local("MoveCount")
    move_count = int(move_count_raw) if move_count_raw.isdigit() else 0
    objects: list[dict[str, str]] = []
    errors: list[str] = []
    for el in root.iter():
        name = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if name != "MoveObject":
            continue
        src_key = ""
        dest_key = ""
        err = ""
        for child in el:
            cname = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if cname == "SourceKey":
                src_key = (child.text or "").strip()
            elif cname == "DestinationKey":
                dest_key = (child.text or "").strip()
            elif cname == "Error":
                err = (child.text or "").strip()
        if err:
            errors.append(f"{src_key}: {err}")
        objects.append({"source_key": src_key, "destination_key": dest_key, "error": err})

    return {
        "move_count": move_count,
        "source_prefix": local("SourcePrefix"),
        "destination_prefix": local("DestinationPrefix"),
        "objects": objects,
        "errors": errors,
    }


class WasabiMoveIncomplete(RuntimeError):
    """A prefix MOVE did not land every object at the destination."""


def list_keys_with_sizes(s3_client, *, bucket: str, prefix: str) -> dict[str, int]:
    """{relative key: size} for real objects under prefix/ (folder placeholders skipped)."""
    root = prefix.strip("/") + "/"
    out: dict[str, int] = {}
    for page in s3_client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=root):
        for obj in page.get("Contents") or []:
            key = str(obj["Key"])
            if key.endswith("/"):
                continue
            out[key[len(root):]] = int(obj.get("Size") or 0)
    return out


def wasabi_move_prefix_verified(
    s3_client,
    *,
    bucket: str,
    source_prefix: str,
    destination_prefix: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """
    MOVE source_prefix/ -> destination_prefix/ and prove it worked.

    wasabi_move_prefix reports per-object failures only in its result's ``errors`` list,
    which callers used to ignore (then updated the ledger / deleted the source prefix).
    This refuses to merge into a non-empty destination unless overwrite=True, then checks
    that every source object exists at the destination with the same size and that the
    source prefix is empty. Raises WasabiMoveIncomplete otherwise (nothing is deleted).
    """
    before = list_keys_with_sizes(s3_client, bucket=bucket, prefix=source_prefix)
    if not before:
        raise WasabiMoveIncomplete(f"nothing to move under s3://{bucket}/{source_prefix.strip('/')}/")
    if not overwrite and list_keys_with_sizes(s3_client, bucket=bucket, prefix=destination_prefix):
        raise WasabiMoveIncomplete(
            f"destination s3://{bucket}/{destination_prefix.strip('/')}/ already has objects"
        )
    result = wasabi_move_prefix(
        s3_client,
        bucket=bucket,
        source_prefix=source_prefix,
        destination_prefix=destination_prefix,
        overwrite=overwrite,
    )
    dest = list_keys_with_sizes(s3_client, bucket=bucket, prefix=destination_prefix)
    left = list_keys_with_sizes(s3_client, bucket=bucket, prefix=source_prefix)
    missing = sorted(rel for rel, size in before.items() if dest.get(rel) != size)
    problems = list(result.get("errors") or [])
    if missing:
        problems.append(f"{len(missing)} object(s) missing/wrong size at destination: {missing[:5]}")
    if left:
        problems.append(f"{len(left)} object(s) still at source: {sorted(left)[:5]}")
    if problems:
        raise WasabiMoveIncomplete(
            f"MOVE s3://{bucket}/{source_prefix.strip('/')}/ -> {destination_prefix.strip('/')}/ "
            "incomplete: " + "; ".join(problems)
        )
    return {**result, "verified_objects": len(before)}
