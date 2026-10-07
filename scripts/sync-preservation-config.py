#!/usr/bin/env python3
"""Write preservation stack env file from private preservation/database.yaml."""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
DB_LOCAL = ROOT / "config" / "preservation" / "database.local.yaml"
OUT = ROOT / "config" / "preservation" / "generated.env"


def private_config_root() -> Path:
    raw = os.environ.get("HITSAVE_PRIVATE_CONFIG", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    sibling = (ROOT.parent / "hitsave-archiver-config").resolve()
    if sibling.is_dir():
        return sibling
    return ROOT


def db_config_path() -> Path:
    private = private_config_root() / "preservation" / "database.yaml"
    if private.is_file():
        return private
    legacy = ROOT / "config" / "preservation" / "database.yaml"
    if legacy.is_file():
        return legacy
    return private


def load_db_config() -> dict:
    db_path = db_config_path()
    if not db_path.is_file():
        print(
            f"Missing {db_path} — run ./scripts/ensure-local-config.sh "
            "(clone hitsave-archiver-config or set HITSAVE_PRIVATE_CONFIG)",
            file=sys.stderr,
        )
        sys.exit(1)
    data = yaml.safe_load(db_path.read_text())
    if DB_LOCAL.is_file():
        local = yaml.safe_load(DB_LOCAL.read_text()) or {}
        if "postgres" in local:
            data.setdefault("postgres", {}).update(local["postgres"])
    return data["postgres"]


def main() -> None:
    pg = load_db_config()
    lines = [
        f"POSTGRES_USER={pg['user']}",
        f"POSTGRES_PASSWORD={pg['password']}",
        f"POSTGRES_DB={pg['database']}",
    ]
    OUT.write_text("\n".join(lines) + "\n")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
