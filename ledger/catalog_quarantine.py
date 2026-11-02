"""Manual quarantine form (wraps scripts/curation-quarantine-omeka.py)."""
from __future__ import annotations

import html
import subprocess
from pathlib import Path
from typing import Any

from catalog_correct import ledger_hint_for_omeka, parse_omeka_item_id

SCRIPT = Path("/app/scripts/curation-quarantine-omeka.py")

BLOCK_REASONS = (
    ("curation_review", "curation_review (generic staff review)"),
    ("manual_quarantine", "manual_quarantine (operator decision)"),
    ("hash_named_images", "hash_named_images (MD5-style filenames)"),
    ("booru_tagged_filenames", "booru_tagged_filenames (booru-style names)"),
    ("not_press_material", "not_press_material (not press-kit material)"),
)


def _field(form: dict[str, list[str]], name: str) -> str:
    return (form.get(name) or [""])[0].strip()


def validate_quarantine_form(
    form: dict[str, list[str]], *, is_post: bool
) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    omeka_ref = _field(form, "omeka_ref")
    if not omeka_ref:
        errors.append("Omeka item id or URL is required.")
    elif parse_omeka_item_id(omeka_ref) is None:
        errors.append("Could not parse Omeka item id from omeka_ref.")

    reason = _field(form, "block_reason") or "curation_review"
    note = _field(form, "note")
    if not note:
        errors.append("Curation note is required (why this item is quarantined).")

    values = {
        "omeka_ref": omeka_ref,
        "block_reason": reason,
        "note": note,
    }
    return values, errors


def build_argv(values: dict[str, str]) -> list[str]:
    return [
        "python",
        str(SCRIPT),
        "--omeka-ref",
        values["omeka_ref"],
        "--reason",
        values["block_reason"],
        values["note"],
    ]


def validate_and_argv(
    form: dict[str, list[str]], *, is_post: bool
) -> tuple[list[str] | None, list[str], dict[str, str]]:
    values, errors = validate_quarantine_form(form, is_post=is_post)
    if errors:
        return None, errors, values
    if not SCRIPT.is_file():
        errors.append(f"Quarantine script not found at {SCRIPT} (check compose mounts).")
        return None, errors, values
    return build_argv(values), errors, values


def run_quarantine(argv: list[str]) -> tuple[int, str]:
    import os

    env = os.environ.copy()
    env.setdefault("HITSAVE_CONFIG_ROOT", "/config")
    env.setdefault("PYTHONPATH", "/app/scripts")
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def render_quarantine_page(
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
        if name == "block_reason" and default:
            return html.escape(default)
        return html.escape(default)

    selected_reason = _field(form, "block_reason") or "curation_review"
    reason_options = ""
    for code, label in BLOCK_REASONS:
        sel = " selected" if code == selected_reason else ""
        reason_options += f'<option value="{html.escape(code)}"{sel}>{html.escape(label)}</option>'

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
<h2>{'Quarantined' if code == 0 else 'Failed'} (exit {code})</h2>
<pre>{html.escape(log or '(no output)')}</pre>
</div>"""

    hint_block = ""
    if hint:
        hint_block = f"""<p class="hint">Ledger: <code>{html.escape(str(hint.get('game_key') or ''))}</code>
 — status {html.escape(str(hint.get('status') or ''))}
 — {html.escape(str(hint.get('moby_title') or ''))}</p>"""

    from site_ui import render_page_shell

    inner = f"""<div class="panel">
<p>Sets ledger <code>status=quarantine</code>, records <code>block_reason</code> and a note,
and hides the Omeka item and DIP media from the public site. Irreversible from this UI — use Omeka admin to re-publish if needed.</p>
{hint_block}
{err_block}
{result_block}
<form method="post" action="/quarantine">
<label class="field">Omeka item id or URL <span class="hint-inline">(required)</span>
<input type="text" name="omeka_ref" value="{val('omeka_ref')}" required></label>
<label class="field">block_reason
<select name="block_reason">{reason_options}</select></label>
<label class="field">Curation note <span class="hint-inline">(required)</span>
<textarea name="note" required>{val('note')}</textarea></label>
<div class="actions"><button type="submit">Quarantine item</button></div>
</form></div>"""
    cfg = status_config or {}
    return render_page_shell(
        title="Quarantine catalog item",
        active="quarantine",
        inner=inner,
        status_config=cfg,
    )
