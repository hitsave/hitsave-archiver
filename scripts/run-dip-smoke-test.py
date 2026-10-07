#!/usr/bin/env python3
"""Smoke-test Omeka DIP Viewer against local Omeka test stack."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIP = ROOT / "fixtures" / "dips" / "built" / "sample-game.tar"
OMEKA = "http://omeka:8080"


def wait_http(url: str, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(3)
    raise SystemExit(f"Timeout waiting for {url}")


def docker_exec(cmd: list[str]) -> str:
    full = [
        "docker",
        "compose",
        "-f",
        str(ROOT / "omeka-test" / "docker-compose.yml"),
        "exec",
        "-T",
        "omeka",
        *cmd,
    ]
    return subprocess.check_output(full, text=True, cwd=ROOT / "omeka-test")


def main() -> None:
    if not DIP.is_file():
        raise SystemExit(f"Missing {DIP}; run scripts/build-fixture-dips.sh first")

    wait_http(f"{OMEKA}/login")

    for mod in ("OmekaDipViewer",):
        out = docker_exec(["omeka-s-cli", "module:state", "--name", mod])
        if "active" not in out.lower():
            docker_exec(["omeka-s-cli", "module:install", "--name", mod])
            docker_exec(["omeka-s-cli", "module:activate", "--name", mod])

    # Parse DIP index using same logic as module (quick validation outside Omeka).
    import tarfile

    with tarfile.open(DIP, "r:") as tf:
        mets = [m for m in tf.getmembers() if m.name == "METS.xml" or m.name.endswith("/METS.xml")]
        if not mets:
            raise SystemExit("Fixture DIP has no root METS.xml")
        print("OK fixture METS:", mets[0].name, "members:", len(tf.getmembers()))

    print("Smoke test passed: Omeka up, modules active, fixture DIP structure valid.")
    print("Manual step: add item in admin with ingester 'DIP package (browse in place)' and upload", DIP)


if __name__ == "__main__":
    main()
