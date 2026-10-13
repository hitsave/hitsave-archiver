"""Shared Omeka catalog registration helpers."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import requests

_SCRIPTS = Path(__file__).resolve().parent


def load_register_dip_omeka_api() -> Any:
    path = _SCRIPTS / "register-dip-omeka-api.py"
    spec = importlib.util.spec_from_file_location("register_dip_omeka_api", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def set_resource_public(
    session: requests.Session,
    base: str,
    creds: dict,
    resource: str,
    resource_id: int,
    *,
    is_public: bool,
    api: Any,
) -> None:
    url = api.api_url(base, f"{resource}/{resource_id}", creds)
    resp = session.patch(
        url,
        headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
        json={"o:is_public": is_public},
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(
            f"PATCH {resource} {resource_id} visibility failed ({resp.status_code}): {resp.text[:800]}"
        )


def ensure_item_set(
    session: requests.Session,
    base: str,
    creds: dict,
    title: str,
    *,
    is_public: bool,
    api: Any,
    sync_visibility_on_existing: bool = False,
) -> int:
    try:
        item_set_id = api.resolve_item_set_id(session, base, creds, title)
    except RuntimeError:
        url = api.api_url(base, "item_sets", creds)
        payload = {"o:title": title, "o:is_public": is_public}
        resp = session.post(
            url,
            json=payload,
            headers={"Accept": "application/ld+json"},
            timeout=120,
        )
        if not resp.ok:
            raise RuntimeError(f"Create item set failed ({resp.status_code}): {resp.text[:800]}")
        item_set_id = int(resp.json()["o:id"])
    elif sync_visibility_on_existing:
        set_resource_public(
            session, base, creds, "item_sets", item_set_id, is_public=is_public, api=api
        )
    return item_set_id
