#!/usr/bin/env python3
"""Create a catalog-only Omeka item for a Redump disc dump from handoff YAML (no Moby)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from handoff_dump_catalog_omeka import apply_handoff_dump_catalog  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

CONFIG_ROOT = Path(__import__("os").environ.get("HITSAVE_CONFIG_ROOT", str(REPO_ROOT / "config")))


def main() -> None:
    if len(sys.argv) not in (2, 4) or (len(sys.argv) == 4 and sys.argv[2] != "--item-id"):
        print(
            "Usage: apply-redump-catalog-omeka.py <hitsave-submission.yaml> [--item-id ID]",
            file=sys.stderr,
        )
        sys.exit(1)
    manifest_path = Path(sys.argv[1])
    patch_item_id = int(sys.argv[3]) if len(sys.argv) == 4 else None
    manifest = load_yaml(manifest_path)
    result = apply_handoff_dump_catalog(
        manifest,
        config_root=CONFIG_ROOT,
        patch_item_id=patch_item_id,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
