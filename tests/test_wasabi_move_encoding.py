"""Wasabi MOVE: key encoding/signing, per-key fallback without a folder placeholder, and
parsing Wasabi's not-quite-XML MoveObjectResult. Behaviour was verified live against Wasabi."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest
from botocore.credentials import Credentials

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import wasabi_rename  # noqa: E402


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def send(self, prepared):
        self.sent.append(prepared)
        status, body = self.responses.pop(0)
        return SimpleNamespace(status_code=status, content=body)


class FakePaginator:
    def __init__(self, keys):
        self.keys = keys

    def paginate(self, Bucket, Prefix):  # noqa: N803
        yield {"Contents": [{"Key": k, "Size": 1} for k in self.keys if k.startswith(Prefix)]}


def fake_client(responses, keys=()):
    http = FakeHttp(responses)
    client = SimpleNamespace(
        meta=SimpleNamespace(endpoint_url="https://s3.us-east-2.wasabisys.com", region_name="us-east-2"),
        _request_signer=SimpleNamespace(_credentials=Credentials("AKIDEXAMPLE", "secret")),
        _endpoint=SimpleNamespace(http_session=http),
        get_paginator=lambda name: FakePaginator(list(keys)),
    )
    return client, http


NAME = "Famitsū Wave DVD (Japan) Tougeki '10 A&B #1 50%"


def test_url_and_destination_are_percent_encoded_once():
    client, http = fake_client([(200, b"")])
    wasabi_rename.wasabi_move_key(client, bucket="b", source_key=f"in/{NAME}.iso", destination_key=f"out/{NAME}.iso")
    req = http.sent[0]
    path = urlsplit(req.url).path
    assert " " not in path and "'" not in path and "ū" not in path
    assert unquote(path) == f"/b/in/{NAME}.iso"  # decodes back to the exact key (single encoding)
    dest = req.headers["Destination"]
    dest = dest.decode() if isinstance(dest, bytes) else dest
    assert dest.isascii() and unquote(dest) == f"out/{NAME}.iso"
    assert "AWS4-HMAC-SHA256" in str(req.headers["Authorization"])


def test_prefix_move_uses_wasabi_prefix_header():
    ok = b'<?xml version="1.0"?><MoveObjectResult><MoveCount>1</MoveCount></MoveObjectResult>'
    client, http = fake_client([(200, ok)])
    res = wasabi_rename.wasabi_move_prefix(client, bucket="b", source_prefix=f"in/{NAME}", destination_prefix="out/x")
    assert res["move_count"] == 1
    assert http.sent[0].headers["X-Wasabi-Prefix"] in ("true", b"true")
    assert len(http.sent) == 1


def test_prefix_move_falls_back_per_key_without_placeholder():
    no_key = b"<Error><Code>NoSuchKey</Code><Key>in/pkg/</Key></Error>"
    keys = ["in/pkg/disc.bin", "in/pkg/sub dir/track 01.cue"]
    client, http = fake_client([(404, no_key), (200, b""), (200, b"")], keys=keys)
    res = wasabi_rename.wasabi_move_prefix(client, bucket="b", source_prefix="in/pkg", destination_prefix="out/pkg")
    assert res["per_key_fallback"] is True
    assert res["move_count"] == 2 and res["errors"] == []
    assert [o["destination_key"] for o in res["objects"]] == ["out/pkg/disc.bin", "out/pkg/sub dir/track 01.cue"]
    assert "X-Wasabi-Prefix" not in http.sent[1].headers


def test_per_key_fallback_reports_failures():
    no_key = b"<Error><Code>NoSuchKey</Code></Error>"
    client, _ = fake_client(
        [(404, no_key), (200, b""), (500, b"<Error><Code>InternalError</Code></Error>")],
        keys=["in/pkg/a", "in/pkg/b"],
    )
    res = wasabi_rename.wasabi_move_prefix(client, bucket="b", source_prefix="in/pkg", destination_prefix="out/pkg")
    assert res["move_count"] == 1
    assert len(res["errors"]) == 1 and res["errors"][0].startswith("in/pkg/b")


def test_missing_prefix_still_raises():
    client, _ = fake_client([(404, b"<Error><Code>NoSuchKey</Code></Error>")], keys=[])
    with pytest.raises(wasabi_rename.ClientError):
        wasabi_rename.wasabi_move_prefix(client, bucket="b", source_prefix="in/none", destination_prefix="out/none")


def test_signature_errors_still_raise():
    client, _ = fake_client([(403, b"<Error><Code>SignatureDoesNotMatch</Code></Error>")])
    with pytest.raises(wasabi_rename.ClientError):
        wasabi_rename.wasabi_move_prefix(client, bucket="b", source_prefix="in/p", destination_prefix="out/p")


def test_parse_result_with_unescaped_key_text():
    # Shape Wasabi actually returned for keys containing '&' and '<>' (not valid XML as sent).
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<MoveObjectResult><SourcePrefix>in/A&B <e>/</SourcePrefix>"
        "<DestinationPrefix>out/A&B <e>/</DestinationPrefix><MoveCount>1</MoveCount>"
        "<MoveObject><SourceKey>in/A&B <e>/x.bin</SourceKey>"
        "<DestinationKey>out/A&B <e>/x.bin</DestinationKey></MoveObject>"
        "<MoveObject><SourceKey>in/Tougeki &apos;10/y.bin</SourceKey>"
        "<DestinationKey>out/Tougeki &apos;10/y.bin</DestinationKey><Error>Bad & worse</Error></MoveObject>"
        "</MoveObjectResult>"
    ).encode()
    res = wasabi_rename._parse_move_result(body)
    assert res["move_count"] == 1
    assert res["source_prefix"] == "in/A&B <e>/"
    assert res["objects"][0]["source_key"] == "in/A&B <e>/x.bin"
    assert res["objects"][1]["source_key"] == "in/Tougeki '10/y.bin"
    assert res["errors"] == ["in/Tougeki '10/y.bin: Bad & worse"]
