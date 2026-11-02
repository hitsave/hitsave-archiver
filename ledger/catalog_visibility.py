"""Omeka visibility toggle (wraps scripts/set-omeka-visibility.py)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from catalog_correct import parse_omeka_item_id

SCRIPT = Path("/app/scripts/set-omeka-visibility.py")


def run_visibility(omeka_ref: str, *, is_public: bool) -> tuple[int, str]:
    import os

    if parse_omeka_item_id(omeka_ref) is None:
        return 1, f"Invalid omeka_ref: {omeka_ref!r}"
    if not SCRIPT.is_file():
        return 1, f"Missing {SCRIPT}"
    flag = "--public" if is_public else "--private"
    env = os.environ.copy()
    env.setdefault("HITSAVE_CONFIG_ROOT", "/config")
    env.setdefault("PYTHONPATH", "/app/scripts")
    proc = subprocess.run(
        ["python", str(SCRIPT), "--omeka-ref", omeka_ref.strip(), flag],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()
