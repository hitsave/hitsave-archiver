"""Optional Windmill API client for completed job list (read-only)."""
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import requests


def read_token_file(path: str) -> str | None:
    p = path.strip()
    if not p:
        return None
    try:
        from pathlib import Path

        text = Path(p).read_text(encoding="utf-8").strip()
        return text or None
    except OSError:
        return None


def windmill_api_settings(ops: dict[str, Any]) -> dict[str, str] | None:
    wm = ops.get("windmill") or {}
    if not isinstance(wm, dict):
        wm = {}
    ui = (ops.get("windmill_ui_url") or wm.get("ui_public_url") or "").strip().rstrip("/")
    workspace = (wm.get("workspace") or ops.get("windmill_workspace") or "").strip()
    api_base = (wm.get("api_url") or "").strip().rstrip("/")
    if not api_base and ui:
        api_base = f"{ui}/api"
    token_file = (wm.get("token_file") or ops.get("windmill_token_file") or "").strip()
    token = read_token_file(token_file) if token_file else None
    if not workspace or not api_base:
        return None
    return {
        "ui_base": ui or api_base.removesuffix("/api"),
        "api_base": api_base,
        "workspace": workspace,
        "token": token or "",
    }


def windmill_script_ui_url(ui_base: str, script_path: str) -> str:
    path = script_path.strip().lstrip("/")
    return f"{ui_base.rstrip('/')}/scripts/get/{path}"


def windmill_job_ui_url(ui_base: str, job_id: str) -> str:
    return f"{ui_base.rstrip('/')}/run/{job_id}"


def fetch_completed_jobs(
    *,
    api_base: str,
    workspace: str,
    token: str,
    per_page: int = 25,
) -> tuple[list[dict[str, Any]], str | None]:
    if not token:
        return [], "Windmill API token not configured (set ops.windmill.token_file)"
    url = urljoin(
        f"{api_base.rstrip('/')}/",
        f"w/{workspace}/jobs/completed/list",
    )
    try:
        resp = requests.get(
            url,
            params={"per_page": per_page, "page": 1},
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return [], str(exc)
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("jobs") or data.get("data") or []
    else:
        rows = []
    jobs: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        jid = row.get("id") or row.get("job") or ""
        script_path = row.get("script_path") or row.get("path") or row.get("flow_path") or ""
        kind = row.get("job_kind") or row.get("kind") or ""
        jobs.append(
            {
                "id": jid,
                "path": script_path or row.get("flow_path") or "—",
                "kind": kind,
                "success": row.get("success"),
                "created_at": row.get("created_at") or row.get("started_at") or "",
                "duration_ms": row.get("duration_ms"),
            }
        )
    return jobs, None


def windmill_quick_links(ui_base: str, scripts: dict[str, str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for label, path in scripts.items():
        if path:
            out.append((label, windmill_script_ui_url(ui_base, path)))
    return out
