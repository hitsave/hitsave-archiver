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
    else:
        if sync_visibility_on_existing:
            set_resource_public(
                session, base, creds, "item_sets", item_set_id, is_public=is_public, api=api
            )
    return item_set_id


def resolve_dump_item_set_id(
    session: requests.Session,
    base: str,
    creds: dict,
    dump_cfg: dict,
    *,
    is_public: bool,
    api: Any,
) -> int:
    configured_id = dump_cfg.get("item_set_id")
    if configured_id is not None:
        return int(configured_id)
    return ensure_item_set(
        session,
        base,
        creds,
        str(dump_cfg["item_set_title"]),
        is_public=is_public,
        api=api,
        sync_visibility_on_existing=True,
    )


def apply_item_item_sets(
    session: requests.Session,
    base: str,
    creds: dict,
    item_id: int,
    item_set_ids: list[int],
    *,
    api: Any,
) -> None:
    """Replace item set membership (Omeka REST PATCH appends by default)."""
    get_url = api.api_url(base, f"items/{item_id}", creds)
    resp = session.get(get_url, headers={"Accept": "application/ld+json"}, timeout=120)
    if not resp.ok:
        raise RuntimeError(
            f"GET item {item_id} failed ({resp.status_code}): {resp.text[:800]}"
        )
    item_body = resp.json()
    put_body = {k: v for k, v in item_body.items() if not str(k).startswith("@")}
    put_body["o:item_set"] = [{"o:id": sid} for sid in item_set_ids]
    put_url = api.api_url(base, f"items/{item_id}", creds)
    put = session.put(
        put_url,
        headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
        json=put_body,
        timeout=120,
    )
    if not put.ok:
        raise RuntimeError(
            f"PUT item {item_id} item sets failed ({put.status_code}): {put.text[:800]}"
        )


def resolve_resource_template(
    session: requests.Session,
    base: str,
    creds: dict,
    label: str,
    *,
    api: Any,
) -> tuple[int, int | None]:
    url = api.api_url(base, "resource_templates", creds)
    resp = session.get(
        url,
        params={"label": label},
        headers={"Accept": "application/ld+json"},
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(
            f"List resource templates failed ({resp.status_code}): {resp.text[:800]}"
        )
    rows = resp.json()
    if not rows:
        raise RuntimeError(f"Resource template not found: {label!r}")
    row = rows[0]
    template_id = int(row["o:id"])
    rc = row.get("o:resource_class") or {}
    class_id = rc.get("o:id")
    return template_id, int(class_id) if class_id is not None else None


def resolve_resource_template_id(
    session: requests.Session,
    base: str,
    creds: dict,
    label: str,
    *,
    api: Any,
) -> int:
    template_id, _ = resolve_resource_template(session, base, creds, label, api=api)
    return template_id


def apply_item_resource_template(
    session: requests.Session,
    base: str,
    creds: dict,
    item_id: int,
    template_id: int,
    *,
    resource_class_id: int | None = None,
    api: Any,
) -> None:
    url = api.api_url(base, f"items/{item_id}", creds)
    payload: dict = {"o:resource_template": {"o:id": template_id}}
    if resource_class_id is not None:
        payload["o:resource_class"] = {"o:id": resource_class_id}
    resp = session.patch(
        url,
        headers={"Accept": "application/ld+json", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(
            f"PATCH item {item_id} resource template failed ({resp.status_code}): {resp.text[:800]}"
        )
