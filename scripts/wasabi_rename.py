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
