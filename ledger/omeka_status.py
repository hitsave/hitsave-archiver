"""Read-only Omeka lookups for the item page (is the catalog record public right now?)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

import yaml

TIMEOUT_S = 5


def _default_config_root() -> Path:
    return Path(os.environ.get("HITSAVE_CONFIG_ROOT", "/config"))


def _api_settings(config_root: Path) -> tuple[str, dict]:
    """(api base url, credentials), resolved like scripts/set-omeka-visibility.py."""
    api_cfg = yaml.safe_load((config_root / "omeka-catalog.yaml").read_text())["api"]
    cred_path = config_root / api_cfg["credentials_file"]
    rel = Path(api_cfg["credentials_file"])
    if rel.parts and rel.parts[0] == "config":
        cred_path = config_root / Path(*rel.parts[1:])
    creds = yaml.safe_load(cred_path.read_text())
    return str(api_cfg["base_url"]).rstrip("/"), creds


def fetch_item_is_public(
    item_id: int,
    *,
    get: Callable | None = None,
    config_root: Path | None = None,
) -> bool | None:
    """o:is_public of the Omeka item, or None when it can't be read (no config, Omeka down, …)."""
    try:
        base, creds = _api_settings(config_root or _default_config_root())
        query = urlencode({"key_identity": creds["key_identity"], "key_credential": creds["key_credential"]})
        if get is None:
            import requests

            get = requests.get
        resp = get(f"{base}/items/{int(item_id)}?{query}", headers={"Accept": "application/json"}, timeout=TIMEOUT_S)
        if resp.status_code != 200:
            return None
        data = resp.json()
        value = data.get("o:is_public") if isinstance(data, dict) else None
        return value if isinstance(value, bool) else None
    except Exception:  # noqa: BLE001 - the page must render whatever goes wrong here
        return None
