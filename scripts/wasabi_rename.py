"""Wasabi S3 extension: rename/move objects by key (HTTP MOVE).

See https://docs.wasabi.com/apidocs/operations-on-objects (Renaming Objects).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError


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

    Uses Wasabi MOVE with X-Wasabi-Prefix: true (folder-style rename).
    """
    src = source_prefix.strip("/") + "/"
    dest = destination_prefix.strip("/") + "/"
    if src == dest:
        return {"move_count": 0, "skipped": True, "errors": []}

    # Path-style URL with literal key bytes; SigV4 must match what Wasabi receives (no pre-encoding).
    endpoint = str(s3_client.meta.endpoint_url).rstrip("/")
    url = f"{endpoint}/{bucket}/{src}"

    headers = {
        "Destination": dest,
        "X-Wasabi-Prefix": "true",
        "X-Wasabi-Quiet": "true" if quiet else "false",
        "Overwrite": "true" if overwrite else "false",
        # SigV4 for bodyless MOVE (same as S3 unsigned payload).
        "X-Amz-Content-Sha256": "UNSIGNED-PAYLOAD",
    }

    request = AWSRequest(method="MOVE", url=url, headers=headers, data=b"")
    credentials = s3_client._request_signer._credentials  # noqa: SLF001
    region = s3_client.meta.region_name
    SigV4Auth(credentials, "s3", region).add_auth(request)
    prepared = request.prepare()

    http = s3_client._endpoint.http_session  # noqa: SLF001
    response = http.send(prepared)
    status = getattr(response, "status_code", None) or response.raw.status
    body = response.content

    if status >= 400:
        raise ClientError(
            {"Error": {"Code": str(status), "Message": body.decode("utf-8", errors="replace")[:500]}},
            "WasabiMove",
        )

    return _parse_move_result(body)


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
    endpoint = str(s3_client.meta.endpoint_url).rstrip("/")
    url = f"{endpoint}/{bucket}/{src}"
    headers = {
        "Destination": dest,
        "X-Wasabi-Quiet": "true",
        "Overwrite": "true" if overwrite else "false",
        "X-Amz-Content-Sha256": "UNSIGNED-PAYLOAD",
    }
    request = AWSRequest(method="MOVE", url=url, headers=headers, data=b"")
    credentials = s3_client._request_signer._credentials  # noqa: SLF001
    region = s3_client.meta.region_name
    SigV4Auth(credentials, "s3", region).add_auth(request)
    prepared = request.prepare()
    http = s3_client._endpoint.http_session  # noqa: SLF001
    response = http.send(prepared)
    status = getattr(response, "status_code", None) or response.raw.status
    body = response.content
    if status >= 400:
        raise ClientError(
            {"Error": {"Code": str(status), "Message": body.decode("utf-8", errors="replace")[:500]}},
            "WasabiMoveKey",
        )


def _parse_move_result(body: bytes) -> dict[str, Any]:
    if not body.strip():
        return {"move_count": 0, "errors": [], "objects": []}
    root = ET.fromstring(body)
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
