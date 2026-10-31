"""Windmill API client (job list + run script by path)."""
from __future__ import annotations

import json
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


def correct_catalog_script_path(ops: dict[str, Any]) -> str:
    wm = ops.get("windmill") or {}
    if not isinstance(wm, dict):
        wm = {}
    explicit = (wm.get("correct_catalog_script") or "").strip()
    if explicit:
        return explicit
    scripts = wm.get("scripts") if isinstance(wm.get("scripts"), dict) else {}
    for key in ("Catalog correction", "Catalog correction (Windmill)"):
        path = scripts.get(key)
        if path:
            return str(path).strip()
    return "f/hitsave/correct_catalog_item"


def windmill_job_wait_timeout_seconds(ops: dict[str, Any]) -> int:
    wm = ops.get("windmill") or {}
    if not isinstance(wm, dict):
        wm = {}
    raw = wm.get("job_wait_timeout_seconds", 900)
    try:
        sec = int(raw)
    except (TypeError, ValueError):
        sec = 900
    return max(30, min(sec, 3600))


def _format_job_result_body(body: Any) -> str:
    if body is None:
        return "(no output)"
    if isinstance(body, str):
        return body.strip() or "(no output)"
    if isinstance(body, (dict, list)):
        return json.dumps(body, indent=2)
    return str(body)


def run_script_by_path_wait_result(
    *,
    api_base: str,
    workspace: str,
    token: str,
    script_path: str,
    args: dict[str, Any],
    timeout_seconds: int,
) -> tuple[int, str]:
    """Run Windmill script synchronously; return (exit_code, log text)."""
    path = script_path.strip().lstrip("/")
    url = urljoin(
        f"{api_base.rstrip('/')}/",
        f"w/{workspace}/jobs/run_wait_result/p/{path}",
    )
    try:
        resp = requests.post(
            url,
            json=args,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=timeout_seconds,
        )
    except requests.Timeout:
        return 1, f"Windmill job timed out after {timeout_seconds}s (increase ops.windmill.job_wait_timeout_seconds)."
    except requests.RequestException as exc:
        return 1, f"Windmill API request failed: {exc}"

    if resp.ok:
        return 0, _format_job_result_body(resp.json() if resp.content else None)

    detail = resp.text.strip()[:4000] or resp.reason
    try:
        err_json = resp.json()
        if isinstance(err_json, dict):
            detail = err_json.get("error") or err_json.get("message") or json.dumps(err_json, indent=2)
    except ValueError:
        pass
    return 1, f"Windmill job failed (HTTP {resp.status_code}): {detail}"


def run_correction_via_windmill(
    ops: dict[str, Any],
    script_args: dict[str, Any],
) -> tuple[int, str]:
    settings = windmill_api_settings(ops)
    if not settings:
        return (
            1,
            "Windmill is not configured. Set ops.windmill.workspace and ops.windmill_ui_url "
            "(or ops.windmill.api_url) in status-web.yaml.",
        )
    token = settings.get("token") or ""
    if not token:
        return (
            1,
            "Windmill API token missing. Set ops.windmill.token_file to a readable bearer token.",
        )
    script_path = correct_catalog_script_path(ops)
    timeout = windmill_job_wait_timeout_seconds(ops)
    code, log = run_script_by_path_wait_result(
        api_base=settings["api_base"],
        workspace=settings["workspace"],
        token=token,
        script_path=script_path,
        args=script_args,
        timeout_seconds=timeout,
    )
    ui = settings.get("ui_base") or ""
    if ui:
        log = (
            f"{log}\n\nScript: {windmill_script_ui_url(ui, script_path)}"
            if log
            else f"Script: {windmill_script_ui_url(ui, script_path)}"
        )
    return code, log
