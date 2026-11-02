"""Catalog correction form; runs Windmill f/hitsave/correct_catalog_item."""
from __future__ import annotations

import html
import re
from typing import Any

from windmill_client import run_correction_via_windmill

CORRECTION_FIELD_NAMES = ("title", "moby_url", "description", "developer", "publisher")
CLEAR_FIELD_NAMES = ("clear_moby_url", "clear_developer", "clear_publisher")
_ITEM_ID_RE = re.compile(r"/item/(\d+)(?:/|$|\?)")


def _field(form: dict[str, list[str]], name: str) -> str:
    return (form.get(name) or [""])[0].strip()


def _form_bool(values: dict[str, str], name: str) -> bool:
    return values.get(name) in ("true", "on", "1", "yes")


def parse_omeka_item_id(ref: str) -> int | None:
    raw = ref.strip()
    if raw.isdigit():
        return int(raw)
    m = _ITEM_ID_RE.search(raw)
    return int(m.group(1)) if m else None


def ledger_hint_for_omeka(connect, omeka_ref: str) -> dict[str, Any] | None:
    item_id = parse_omeka_item_id(omeka_ref)
    if item_id is None:
        return None
    sql = """
        SELECT game_key, omeka_item_id, moby_title, status, aip_wasabi_prefix
        FROM game_ingest
        WHERE omeka_item_id = %s
        LIMIT 1
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (item_id,))
            row = cur.fetchone()
    if not row:
        return None
    cols = ["game_key", "omeka_item_id", "moby_title", "status", "aip_wasabi_prefix"]
    return dict(zip(cols, row))


def validate_correction_form(
    form: dict[str, list[str]], *, is_post: bool
) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    omeka_ref = _field(form, "omeka_ref")
    if not omeka_ref:
        errors.append("Omeka item id or URL is required.")
    values: dict[str, str] = {"omeka_ref": omeka_ref}
    for name in CORRECTION_FIELD_NAMES:
        values[name] = _field(form, name)
    values["former_title"] = _field(form, "former_title")
    values["correction_summary"] = _field(form, "correction_summary")
    for name in CLEAR_FIELD_NAMES:
        values[name] = _field(form, name) if is_post else ""
    values["reset_ledger_moby"] = _field(form, "reset_ledger_moby") if is_post else "true"

    has_correction = any(values[name] for name in CORRECTION_FIELD_NAMES if name != "description")
    if values.get("description", "").strip():
        has_correction = True
    if any(_field(form, name) in ("true", "on", "1", "yes") for name in CLEAR_FIELD_NAMES):
        has_correction = True
    if not errors and not has_correction:
        errors.append(
            "Provide at least one field to change or clear (title, Moby URL, description, "
            "developer, publisher, or a clear checkbox)."
        )
    if is_post:
        execute = _field(form, "execute") in ("true", "on", "1", "yes")
    else:
        execute = True
    values["execute"] = "true" if execute else "false"
    return values, errors


def description_for_windmill(values: dict[str, str]) -> str | None:
    """Description override only when non-empty (omit so clear_moby restores press-kit text)."""
    desc = (values.get("description") or "").strip()
    if not desc:
        return None
    return desc


def build_windmill_args(values: dict[str, str]) -> dict[str, Any]:
    """JSON body for Windmill correct_catalog_item (matches script schema)."""
    args: dict[str, Any] = {"omeka_ref": values["omeka_ref"]}
    if values.get("title"):
        args["title"] = values["title"]
    if values.get("moby_url"):
        args["moby_url"] = values["moby_url"]
    if _form_bool(values, "clear_moby_url"):
        args["clear_moby_url"] = True
    desc = description_for_windmill(values)
    if desc is not None:
        args["description"] = desc
    if values.get("developer"):
        args["developer"] = values["developer"]
    if _form_bool(values, "clear_developer"):
        args["clear_developer"] = True
    if values.get("publisher"):
        args["publisher"] = values["publisher"]
    if _form_bool(values, "clear_publisher"):
        args["clear_publisher"] = True
    if values.get("former_title"):
        args["former_title"] = values["former_title"]
    if values.get("correction_summary"):
        args["correction_summary"] = values["correction_summary"]
    if values.get("reset_ledger_moby") == "false":
        args["reset_ledger_moby"] = False
    if values.get("execute") != "true":
        args["execute"] = False
    return args


def validate_and_job_args(
    form: dict[str, list[str]], *, is_post: bool
) -> tuple[dict[str, Any] | None, list[str], dict[str, str]]:
    values, errors = validate_correction_form(form, is_post=is_post)
    if errors:
        return None, errors, values
    return build_windmill_args(values), errors, values


def run_correction(ops: dict[str, Any], script_args: dict[str, Any]) -> tuple[int, str]:
    return run_correction_via_windmill(ops, script_args)


def render_correct_page(
    *,
    form: dict[str, list[str]] | None = None,
    hint: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    result: tuple[int, str] | None = None,
    is_post: bool = False,
    status_config: dict | None = None,
) -> bytes:
    form = form or {}
    errors = errors or []

    def val(name: str, default: str = "") -> str:
        if name in form:
            return html.escape(_field(form, name))
        if name == "omeka_ref" and hint and hint.get("omeka_item_id"):
            return html.escape(str(hint["omeka_item_id"]))
        return html.escape(default)

    if is_post and form:
        execute_checked = _field(form, "execute") in ("true", "on", "1", "yes")
    else:
        execute_checked = True

    err_block = ""
    if errors:
        err_block = (
            '<div class="errors"><ul>'
            + "".join(f"<li>{html.escape(e)}</li>" for e in errors)
            + "</ul></div>"
        )

    result_block = ""
    if result is not None:
        code, log = result
        cls = "ok" if code == 0 else "fail"
        result_block = f"""<div class="result {cls}">
<h2>{'Success' if code == 0 else 'Failed'} (exit {code})</h2>
<pre>{html.escape(log or '(no output)')}</pre>
</div>"""

    hint_block = ""
    if hint:
        hint_block = f"""<p class="hint">Ledger: <code>{html.escape(str(hint.get('game_key') or ''))}</code>
 — {html.escape(str(hint.get('moby_title') or ''))}
 — status {html.escape(str(hint.get('status') or ''))}</p>"""

    from site_ui import render_page_shell

    inner = f"""<div class="panel">
<p>Submits <code>f/hitsave/correct_catalog_item</code> on Windmill (same bash wrapper as the Windmill UI).
Omeka and optional Wasabi bag updates run on the hitsave worker.</p>
{hint_block}
{err_block}
{result_block}
<form method="post" action="/correct">
<label class="field">Omeka item id or URL <span class="hint-inline">(required)</span>
<input type="text" name="omeka_ref" value="{val('omeka_ref')}" required></label>
<label class="field">Title
<input type="text" name="title" value="{val('title')}"></label>
<label class="field">MobyGames URL (bibo:uri)
<input type="text" name="moby_url" value="{val('moby_url')}"></label>
<label class="field"><input type="checkbox" name="clear_moby_url" value="true">
 Clear MobyGames catalog (URL, Moby description/rights, developer/publisher from match; resets ledger)</label>
<label class="field">Description <span class="hint-inline">(optional; leave blank when clearing Moby to restore press-kit text)</span>
<textarea name="description">{val('description')}</textarea></label>
<label class="field">Developer(s) <span class="hint-inline">(comma-separated)</span>
<input type="text" name="developer" value="{val('developer')}"></label>
<label class="field"><input type="checkbox" name="clear_developer" value="true">
 Clear developer(s)</label>
<label class="field">Publisher(s) <span class="hint-inline">(comma-separated)</span>
<input type="text" name="publisher" value="{val('publisher')}"></label>
<label class="field"><input type="checkbox" name="clear_publisher" value="true">
 Clear publisher(s)</label>
<label class="field">Former title <span class="hint-inline">(bag notes; default current title)</span>
<input type="text" name="former_title" value="{val('former_title')}"></label>
<label class="field">Correction summary <span class="hint-inline">(bag catalog_notes)</span>
<textarea name="correction_summary">{val('correction_summary')}</textarea></label>
<label class="field"><input type="checkbox" name="execute" value="true"{" checked" if execute_checked else ""}>
 Upload bag metadata to Wasabi when title or Moby URL changes</label>
<div class="actions"><button type="submit">Apply correction</button></div>
</form></div>"""
    cfg = status_config or {}
    return render_page_shell(title="Correct catalog metadata", active="correct", inner=inner, status_config=cfg)
