#!/usr/bin/env python3
"""Print shell export lines for Omeka/MariaDB keys from settings.yaml."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml


def main() -> None:
    path = Path(sys.argv[1])
    data = yaml.safe_load(path.read_text())
    db = data["database"]
    omeka = data["omeka"]
    pairs = {
        "MARIADB_DATABASE": db["name"],
        "MARIADB_USER": db["user"],
        "MARIADB_PASSWORD": db["password"],
        "MARIADB_ROOT_PASSWORD": db["root_password"],
        "OMEKA_ADMIN_EMAIL": omeka["admin_email"],
        "OMEKA_ADMIN_PASSWORD": omeka["admin_password"],
        "OMEKA_SITE_TITLE": omeka["site_title"],
        "DB_HOST": db["host"],
        "DB_NAME": db["name"],
        "DB_USER": db["user"],
        "DB_PASSWORD": db["password"],
    }
    for key, value in pairs.items():
        escaped = str(value).replace("'", "'\\''")
        print(f"export {key}='{escaped}'")


if __name__ == "__main__":
    main()
