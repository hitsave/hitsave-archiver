"""A clamd restart (container recreated, Docker upgrade) costs a wait and a rescan, not an ingest."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import clamav_full_scan as cfs  # noqa: E402
import premis_virus_check as pvc  # noqa: E402


class NetClamd:
    host, port = "clamav", 3310


def _flaky_instream(failures: int, seen: list[bytes]):
    """_instream stand-in: reads the whole stream, then drops the connection ``failures`` times."""
    calls = {"n": 0}

    def fake(host, port, reader, *, block=1 << 20):
        data = b""
        while chunk := reader.read(block):
            data += chunk
        seen.append(data)
        calls["n"] += 1
        if calls["n"] <= failures:
            raise ConnectionResetError(104, "Connection reset by peer")
        return None

    return fake


def test_reset_mid_scan_waits_and_rescans_the_whole_range(tmp_path, monkeypatch):
    payload = bytes(range(256)) * 4000
    f = tmp_path / "a.bin"
    f.write_bytes(payload)
    seen: list[bytes] = []
    waited: list[tuple] = []
    monkeypatch.setattr(cfs, "_instream", _flaky_instream(1, seen))
    monkeypatch.setattr(cfs, "wait_for_clamd_tcp", lambda host, port: waited.append((host, port)))
    res = cfs.scan_path(NetClamd(), f, "a.bin")
    assert waited == [("clamav", 3310)]
    assert seen == [payload, payload]  # the second attempt streamed every byte again
    assert res.sha256 == hashlib.sha256(payload).hexdigest() and res.fully_scanned


def test_gives_up_after_three_attempts(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 100)
    monkeypatch.setattr(cfs, "_instream", _flaky_instream(5, []))
    monkeypatch.setattr(cfs, "wait_for_clamd_tcp", lambda host, port: None)
    with pytest.raises(ConnectionResetError):
        cfs.scan_path(NetClamd(), f, "a.bin")


def test_stream_that_cannot_rewind_is_not_retried(monkeypatch):
    class Pipe:  # like an archive member read from 7-Zip: no restart()
        def __init__(self):
            self.left = b"abc"

        def read(self, n=-1):
            out, self.left = self.left, b""
            return out

    monkeypatch.setattr(cfs, "_instream", _flaky_instream(1, []))
    monkeypatch.setattr(cfs, "wait_for_clamd_tcp", lambda host, port: pytest.fail("must not wait"))
    with pytest.raises(ConnectionResetError):
        cfs._stream(NetClamd(), Pipe())


def test_wait_for_clamd_tcp_returns_on_pong_and_times_out():
    class Sock:
        def __init__(self, reply):
            self.reply = reply

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def sendall(self, data):
            assert data == b"zPING\0"

        def recv(self, n):
            return self.reply

    tries = []

    def connect(addr, timeout):
        tries.append(addr)
        if len(tries) < 3:
            raise ConnectionRefusedError(111, "refused")
        return Sock(b"PONG\0")

    clock = {"t": 0.0}
    cfs.wait_for_clamd_tcp("clamav", 3310, timeout=60, connect=connect,
                           sleep=lambda s: clock.__setitem__("t", clock["t"] + s), now=lambda: clock["t"])
    assert len(tries) == 3

    clock["t"] = 0.0

    def never(addr, timeout):
        raise ConnectionRefusedError(111, "refused")

    with pytest.raises(ConnectionError, match="not back after 60s"):
        cfs.wait_for_clamd_tcp("clamav", 3310, timeout=60, connect=never,
                               sleep=lambda s: clock.__setitem__("t", clock["t"] + s), now=lambda: clock["t"])


def test_version_lookup_waits_for_clamd_once(monkeypatch):
    class Clamd(NetClamd):
        calls = 0

        def version(self):
            Clamd.calls += 1
            if Clamd.calls == 1:
                raise OSError("Could not reach clamd using network (clamav, 3310)")  # pyclamd.ConnectionError is an OSError
            return "ClamAV 1.5.1/28191/Sun Nov 22 08:00:00 2026"

    waited = []
    monkeypatch.setattr(cfs, "wait_for_clamd_tcp", lambda host, port: waited.append((host, port)))
    program, version, defs = pvc.clamav_version_info(Clamd())
    assert waited == [("clamav", 3310)] and defs == "28191/Sun Nov 22 08:00:00 2026"


def test_version_lookup_without_network_client_raises(monkeypatch):
    class Local:
        def version(self):
            raise OSError("down")

    with pytest.raises(OSError):
        pvc.clamav_version_info(Local())
