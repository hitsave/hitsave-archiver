"""Catalog correction pages: prefilled form → review → apply (Windmill f/hitsave/correct_catalog_item).

The ledger reads the item from Omeka to prefill the form and to show the change before it runs;
the Windmill script does the write (a full-item PATCH).
"""
from __future__ import annotations

import html
import re
from typing import Any, Callable

from correct_review import (
    FIELD_LABELS,
    FIELDS,
    Change,
    changed_since,
    current_values,
    plan_changes,
    side_effects,
    windmill_args,
)
from formatting import item_href, status_badge
from windmill_client import run_correction_via_windmill

_ITEM_ID_RE = re.compile(r"/item/(\d+)(?:/|$|\?)")
_TRUE = ("true", "on", "1", "yes")

FetchItem = Callable[[int], "dict | None"]
HintFor = Callable[[str], "dict[str, Any] | None"]
RunFn = Callable[[dict[str, Any]], "tuple[int, str]"]


def _field(form: dict[str, list[str]], name: str) -> str:
    return (form.get(name) or [""])[0]


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


def run_correction(ops: dict[str, Any], script_args: dict[str, Any]) -> tuple[int, str]:
    return run_correction_via_windmill(ops, script_args)


# --- rendering -------------------------------------------------------------------------------

def _shell(inner: str, status_config: dict) -> bytes:
    from site_ui import render_page_shell

    return render_page_shell(
        title="Correct catalog metadata", active="correct", inner=inner, status_config=status_config
    )


def _errors(errors: list[str]) -> str:
    if not errors:
        return ""
    return '<div class="errors"><ul>' + "".join(f"<li>{html.escape(e)}</li>" for e in errors) + "</ul></div>"


def _hidden(name: str, value: str) -> str:
    return f'<input type="hidden" name="{name}" value="{html.escape(value)}">'


def _item_line(item_id: int, hint: dict[str, Any] | None) -> str:
    parts = [f"Omeka item <strong>{item_id}</strong>"]
    if hint and hint.get("game_key"):
        key = str(hint["game_key"])
        parts.append(f'<a href="{html.escape(item_href(key))}"><code>{html.escape(key)}</code></a>')
        parts.append(status_badge(hint.get("status")))
    else:
        parts.append('<span class="muted">no ledger row has this item</span>')
    return f'<p class="hint">{" · ".join(parts)}</p>'


def render_correct_lookup(*, omeka_ref: str = "", error: str = "", status_config: dict) -> bytes:
    inner = f"""<div class="panel">
<p>Open the catalog item you want to correct. The form shows what the catalog has now; you change
what's wrong and check the changes before anything is saved.</p>
{_errors([error] if error else [])}
<form method="get" action="/correct">
<label class="field">Omeka item id or URL
<input type="text" name="omeka_ref" value="{html.escape(omeka_ref)}" required></label>
<div class="actions"><button type="submit">Open</button></div>
</form></div>"""
    return _shell(inner, status_config)


def render_correct_form(
    *,
    item_id: int,
    current: dict[str, str],
    values: dict[str, str],
    hint: dict[str, Any] | None,
    errors: list[str] | None = None,
    warning: str = "",
    status_config: dict,
) -> bytes:
    def text(name: str, label: str, hint_text: str = "") -> str:
        extra = f' <span class="hint-inline">{hint_text}</span>' if hint_text else ""
        return (
            f'<label class="field">{label}{extra}\n'
            f'<input type="text" name="{name}" value="{html.escape(values.get(name, ""))}"></label>'
        )

    def checkbox(name: str, checked: bool, label: str) -> str:
        return (
            f'<label class="field"><input type="checkbox" name="{name}" value="true"'
            f'{" checked" if checked else ""}> {label}</label>'
        )

    warn = f'<div class="notice notice-fail" role="status">{html.escape(warning)}</div>' if warning else ""
    origs = "".join(_hidden(f"orig_{f}", current.get(f, "")) for f in FIELDS)
    inner = f"""<div class="panel">
{_item_line(item_id, hint)}
<p>The fields show what the catalog has now. Change only what's wrong; you'll see the changes
before anything is saved.</p>
{warn}
{_errors(errors or [])}
<form method="post" action="/correct">
{_hidden("omeka_ref", str(item_id))}
{origs}
{text("title", "Title")}
{text("moby_url", "MobyGames URL", "(the right game's page on mobygames.com)")}
{checkbox("clear_moby", values.get("clear_moby") in _TRUE,
          "Remove the MobyGames match <span class=\"hint-inline\">(the archive linked the wrong game; "
          "removes the link and what came with it)</span>")}
<label class="field">Description
<textarea name="description" rows="6">{html.escape(values.get("description", ""))}</textarea></label>
{text("developer", "Developers", "(comma-separated; empty the box to remove them all)")}
{text("publisher", "Publishers", "(comma-separated; empty the box to remove them all)")}
<details class="form-more"><summary>Notes kept with the package</summary>
{text("former_title", "Former title", "(default: the current title)")}
<label class="field">Correction summary <span class="hint-inline">(the ledger adds your email address)</span>
<textarea name="correction_summary" rows="3">{html.escape(values.get("correction_summary", ""))}</textarea></label>
</details>
{checkbox("execute", values.get("execute", "true") in _TRUE,
          "Update the package's details on Wasabi when the title or MobyGames link changes")}
<div class="actions"><button type="submit" name="step" value="review">Review changes</button>
<a href="{html.escape(item_href(str(hint["game_key"])) if hint and hint.get("game_key") else "/")}">Cancel</a></div>
</form></div>"""
    return _shell(inner, status_config)


def _cell(value: str, *, removed: bool = False) -> str:
    if removed:
        return '<span class="muted">(removed)</span>'
    if not value:
        return '<span class="muted">—</span>'
    return f'<div class="diff-val">{html.escape(value)}</div>'


def render_correct_review(
    *,
    item_id: int,
    changes: list[Change],
    clear_moby: bool,
    effects: list[str],
    values: dict[str, str],
    orig: dict[str, str],
    hint: dict[str, Any] | None,
    status_config: dict,
) -> bytes:
    rows = []
    for c in changes:
        rows.append(
            f"<tr><th scope=\"row\">{FIELD_LABELS[c.field]}</th><td class=\"diff-before\" data-label=\"Now\">{_cell(c.before)}</td>"
            f"<td class=\"diff-after\" data-label=\"After\">{_cell(c.after, removed=c.action == 'clear')}</td></tr>"
        )
    if clear_moby:
        rows.append(
            f"<tr><th scope=\"row\">{FIELD_LABELS['moby_url']}</th><td class=\"diff-before\" data-label=\"Now\">{_cell(orig.get('moby_url', ''))}</td>"
            f"<td class=\"diff-after\" data-label=\"After\">{_cell('', removed=True)}</td></tr>"
        )
    effects_html = (
        "<h2>Also happens</h2><ul>" + "".join(f"<li>{html.escape(e)}</li>" for e in effects) + "</ul>"
        if effects
        else ""
    )
    summary = values.get("correction_summary", "").strip()
    notes = f"<p>Correction summary: {html.escape(summary)}</p>" if summary else ""
    carried = [_hidden("omeka_ref", str(item_id))]
    carried += [_hidden(f, values.get(f, "")) for f in FIELDS]
    carried += [_hidden(f"orig_{f}", orig.get(f, "")) for f in FIELDS]
    carried += [_hidden(n, values.get(n, "")) for n in ("former_title", "correction_summary")]
    carried.append(_hidden("execute", "true" if values.get("execute", "true") in _TRUE else "false"))
    if clear_moby:
        carried.append(_hidden("clear_moby", "true"))
    inner = f"""<div class="panel">
{_item_line(item_id, hint)}
<h2>Check the changes</h2>
<div class="table-wrap"><table class="diff"><thead><tr><th>Field</th><th>Now</th><th>After</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
{effects_html}
{notes}
<form method="post" action="/correct">
{''.join(carried)}
<div class="actions"><button type="submit" name="step" value="apply" data-busy="Running…">Apply correction</button>
<button type="submit" name="step" value="edit" class="btn-secondary">Back to edit</button></div>
</form></div>"""
    return _shell(inner, status_config)


def render_correct_result(
    *, item_id: int, code: int, log: str, hint: dict[str, Any] | None, status_config: dict
) -> bytes:
    ok = code == 0
    back = (
        f'<a href="{html.escape(item_href(str(hint["game_key"])))}">Back to the item page</a>'
        if hint and hint.get("game_key")
        else '<a href="/">Back to the ledger</a>'
    )
    inner = f"""<div class="panel">
{_item_line(item_id, hint)}
<div class="result {'ok' if ok else 'fail'}">
<h2>{'Correction applied' if ok else 'Correction failed'}</h2>
<pre>{html.escape(log or '(no output)')}</pre>
</div>
<p class="actions-row">{back} <a href="/correct?omeka_ref={item_id}">Correct this item again</a></p>
</div>"""
    return _shell(inner, status_config)


# --- flow ------------------------------------------------------------------------------------

def _unreadable(item_id: int) -> str:
    return (
        f"Couldn't read item {item_id} from Omeka, so the form can't show its current values. "
        "Check the id and try again in a minute; if it keeps failing, ask an admin."
    )


def _values_from_form(form: dict[str, list[str]]) -> dict[str, str]:
    names = (*FIELDS, "former_title", "correction_summary", "clear_moby", "execute")
    values = {n: _field(form, n) for n in names}
    values["execute"] = "true" if values["execute"] in _TRUE else "false"
    return values


def correct_get(
    qs: dict[str, list[str]],
    *,
    fetch_item: FetchItem,
    hint_for: HintFor,
    status_config: dict,
    **_: Any,
) -> bytes:
    ref = _field(qs, "omeka_ref").strip()
    if not ref:
        return render_correct_lookup(status_config=status_config)
    item_id = parse_omeka_item_id(ref)
    if item_id is None:
        return render_correct_lookup(
            omeka_ref=ref, error="Not an Omeka item id or item URL.", status_config=status_config
        )
    item = fetch_item(item_id)
    if item is None:
        return render_correct_lookup(omeka_ref=ref, error=_unreadable(item_id), status_config=status_config)
    current = current_values(item)
    values = dict(current, former_title="", correction_summary="", clear_moby="", execute="true")
    return render_correct_form(
        item_id=item_id, current=current, values=values, hint=hint_for(str(item_id)), status_config=status_config
    )


def correct_post(
    form: dict[str, list[str]],
    *,
    fetch_item: FetchItem,
    hint_for: HintFor,
    run: RunFn,
    audit: Callable[[str], None],
    status_config: dict,
) -> bytes:
    ref = _field(form, "omeka_ref").strip()
    item_id = parse_omeka_item_id(ref)
    if item_id is None:
        return render_correct_lookup(
            omeka_ref=ref, error="Not an Omeka item id or item URL.", status_config=status_config
        )
    step = _field(form, "step")
    values = _values_from_form(form)
    orig = {f: _field(form, f"orig_{f}") for f in FIELDS}
    hint = hint_for(str(item_id))

    def back_to_form(errors=None, warning="", current=orig):
        return render_correct_form(
            item_id=item_id, current=current, values=values, hint=hint,
            errors=errors, warning=warning, status_config=status_config,
        )

    if step == "edit":
        return back_to_form()

    item = fetch_item(item_id)
    if item is None:
        return back_to_form(errors=[_unreadable(item_id)])
    fresh = current_values(item)
    stale = changed_since(orig, fresh)
    if stale:
        for f in stale:
            if values.get(f, "") == orig.get(f, ""):
                values[f] = fresh[f]  # untouched: show what Omeka has now
        labels = ", ".join(FIELD_LABELS[f] for f in stale)
        warning = (
            f"Someone changed {labels} in Omeka since you opened this form, so nothing was saved. "
            "The form now shows the current values, with your own edits kept. Check it and review again."
        )
        return back_to_form(warning=warning, current=fresh)

    clear_moby = values["clear_moby"] in _TRUE
    execute = values["execute"] == "true"
    changes, errors = plan_changes(fresh, values, clear_moby=clear_moby)
    if errors:
        return back_to_form(errors=errors)

    if step != "apply":
        return render_correct_review(
            item_id=item_id, changes=changes, clear_moby=clear_moby,
            effects=side_effects(changes, clear_moby=clear_moby, execute=execute),
            values=values, orig=fresh, hint=hint, status_config=status_config,
        )

    args = windmill_args(
        str(item_id), changes, clear_moby=clear_moby,
        former_title=values["former_title"], correction_summary=values["correction_summary"], execute=execute,
    )
    audit(str(item_id))
    code, log = run(args)
    return render_correct_result(item_id=item_id, code=code, log=log, hint=hint, status_config=status_config)
