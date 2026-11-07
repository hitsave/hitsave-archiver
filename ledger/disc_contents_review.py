"""Disc contents of data-disc dumps waiting for a staff decision (scripts/disc_contents_policy.py:
promotional discs go to review; labelled press kits are public on their own)."""
from __future__ import annotations

import html
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from formatting import fmt_ts, item_href
from site_ui import render_page_shell

ConnectFn = Callable[[], Any]
SCRIPT = Path("/app/scripts/set-disc-contents-display.py")
KIND_LABELS = (("image", "images"), ("video", "videos"), ("document", "documents"), ("audio", "audio"),
               ("program", "programs"), ("other", "other files"))

_INTRO = """<h1>Disc contents</h1>
<p class="muted">Files extracted from data discs (PC and Mac CDs) that aren't labelled press kits.
Check them in the Omeka admin, then publish them or keep them private. Labelled press kits are
published on their own; games, demos and other software never are.</p>"""


def pending(connect: ConnectFn) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT game_key, omeka_item_id, disc_contents_class, disc_contents_display, disc_contents_reasons, "
            "disc_contents_media_id, disc_contents_summary, disc_contents_updated_at FROM game_ingest "
            "WHERE disc_contents_display = 'review' ORDER BY disc_contents_updated_at, id"
        )
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


def recent(connect: ConnectFn, limit: int = 20) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT game_key, omeka_item_id, disc_contents_class, disc_contents_display, disc_contents_updated_at "
            "FROM game_ingest WHERE disc_contents_display IN ('public', 'none') "
            "ORDER BY disc_contents_updated_at DESC NULLS LAST LIMIT %s",
            (limit,),
        )
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


def summary_text(summary: Any) -> str:
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except ValueError:
            summary = None
    if not isinstance(summary, dict):
        return "not extracted yet"
    by_kind = summary.get("by_kind") or {}
    parts = [f"{int(by_kind[k]['files'])} {label}" for k, label in KIND_LABELS if by_kind.get(k, {}).get("files")]
    mb = int(summary.get("bytes") or 0) / 1e6
    return f"{', '.join(parts) or 'no files'} · {mb:,.0f} MB"


_BADGES = {"public": ("badge-ok", "public"), "none": ("badge-neutral", "private"), "review": ("badge-warn", "needs review")}


def _display_badge(display: str | None) -> str:
    cls, label = _BADGES.get(display or "", ("badge-neutral", display or "—"))
    return f'<span class="badge {cls}">{html.escape(label)}</span>'


def render_disc_contents_page(*, rows: list[dict], decided: list[dict], status_config: dict, admin_base: str) -> bytes:
    cards = []
    for row in rows:
        key = str(row["game_key"])
        item_id = row.get("omeka_item_id")
        admin = (f'<a href="{html.escape(f"{admin_base}/item/{int(item_id)}/show")}" target="_blank" '
                 f'rel="noopener">Omeka admin ↗</a>') if item_id else '<span class="muted">no Omeka item</span>'
        ready = bool(row.get("disc_contents_media_id"))
        buttons = (
            f"""<form method="post" action="/disc-contents/decide" class="actions">
<input type="hidden" name="game_key" value="{html.escape(key)}">
<button type="submit" name="display" value="public" data-busy="Publishing…">Publish contents</button>
<button type="submit" name="display" value="none" class="btn-secondary" data-busy="Saving…">Keep private</button>
</form>"""
            if ready
            else '<p class="muted">The contents are still being extracted or attached; check back after the next sweep.</p>'
        )
        cards.append(f"""<div class="panel">
<h2><a href="{html.escape(item_href(key))}"><code>{html.escape(key)}</code></a></h2>
<p>{_display_badge(row.get("disc_contents_display"))} · {html.escape(summary_text(row.get("disc_contents_summary")))} · {admin}</p>
<p class="muted">{html.escape(str(row.get("disc_contents_reasons") or ""))}</p>
{buttons}
</div>""")
    body = "\n".join(cards) or '<div class="panel"><p>Nothing waiting for review.</p></div>'
    done = "".join(
        f'<tr><td><a href="{html.escape(item_href(str(r["game_key"])))}"><code>{html.escape(str(r["game_key"]))}</code></a></td>'
        f"<td>{_display_badge(r.get('disc_contents_display'))}</td><td>{html.escape(str(r.get('disc_contents_class') or ''))}</td>"
        f"<td>{html.escape(fmt_ts(r.get('disc_contents_updated_at')))}</td></tr>"
        for r in decided
    )
    table = (f"""<h2>Recently decided</h2><table class="kv"><thead><tr><th>Package</th><th>Contents</th><th>Kind</th>
<th>Updated</th></tr></thead><tbody>{done}</tbody></table>""" if done else "")
    inner = f"{_INTRO}\n<p class=\"muted\">{len(rows)} waiting</p>\n{body}\n{table}"
    return render_page_shell(title="Disc contents", active="disc", inner=inner, status_config=status_config)


def run_decision(game_key: str, display: str, user: str) -> tuple[int, str]:
    if display not in ("public", "none"):
        return 1, f"Unknown decision {display!r}"
    if not SCRIPT.is_file():
        return 1, f"Missing {SCRIPT}"
    env = os.environ.copy()
    env.setdefault("HITSAVE_CONFIG_ROOT", "/config")
    env.setdefault("PYTHONPATH", "/app/scripts")
    proc = subprocess.run(
        ["python", str(SCRIPT), "--game-key", game_key, "--display", display, "--user", user],
        capture_output=True, text=True, timeout=120, env=env,
    )
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()
