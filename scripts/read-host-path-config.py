#!/usr/bin/env python3
"""Print one host_paths.* value for shell scripts (repo-relative paths expanded)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from host_paths import resolve_host_path  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: read-host-path-config.py <host_paths_key>")
    print(resolve_host_path(sys.argv[1], repo_root=ROOT))


if __name__ == "__main__":
    main()
