#!/usr/bin/env python3
"""Backward-compatible alias for apply-dump-catalog-omeka.py --manifest …"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from apply_dump_catalog_omeka import main  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: apply-redump-catalog-omeka.py <handoff.yaml> [--item-id ID]\n"
            "Prefer: apply-dump-catalog-omeka.py --manifest <handoff.yaml> [--item-id ID]",
            file=sys.stderr,
        )
        sys.exit(1)
    rewritten = [sys.argv[0], "--manifest", *sys.argv[1:]]
    sys.argv = rewritten
    main()
