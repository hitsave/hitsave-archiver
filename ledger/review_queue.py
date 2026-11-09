"""Review queue: hidden catalog records of complete packages, one at a time, to check and publish."""
from __future__ import annotations

import html
import threading
import time
from typing import Any, Callable
from urllib.parse import urlencode

from correct_review import current_values
from damaged_panel import render_damaged_panel
from formatting import item_href, moby_badge, status_badge
from site_ui import render_page_shell

ConnectFn = Callable[[], Any]
MOBY_FILTERS = {"ok": "MobyGames match", "ambiguous": "ambiguous match", "no_match": "no MobyGames match"}
DESCRIPTION_MAX = 6000


class HiddenItems:
    """Ids of hidden Omeka items, cached; the ledger updates it itself when it publishes or hides."""

    def __init__(
        self,
        fetch: Callable[[], "set[int] | None"],
        *,
        ttl: float = 600,
        clock: Callable[[], float] = time.monotonic,
        spawn: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl
        self._clock = clock
        self._spawn = spawn  # None: refresh inline (tests); app passes a thread starter
        self._ids: set[int] | None = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()
        self._refreshing = False

    def _refresh(self) -> None:
        fresh = self._fetch()
        with self._lock:
            self._refreshing = False
            self._loaded_at = self._clock()  # a failed refresh waits a full ttl too, keeping the last good set
            if fresh is not None:
                self._ids = set(fresh)

    def ids(self) -> set[int] | None:
        with self._lock:
            stale = self._ids is None or self._clock() - self._loaded_at >= self._ttl
            start = stale and not self._refreshing
            if start:
                self._refreshing = True
            have = self._ids is not None
        if start:
            if have and self._spawn is not None:
                self._spawn(self._refresh)
            else:
                self._refresh()
        with self._lock:
            return set(self._ids) if self._ids is not None else None

    def mark_public(self, item_id: int) -> None:
        with self._lock:
            if self._ids is not None:
                self._ids.discard(int(item_id))

    def mark_private(self, item_id: int) -> None:
        with self._lock:
            if self._ids is not None:
                self._ids.add(int(item_id))


def parse_queue_args(qs: dict[str, list[str]]) -> tuple[str, int]:
    moby = (qs.get("moby") or [""])[0].strip()
    if moby not in MOBY_FILTERS:
        moby = ""
    try:
        after = max(0, int((qs.get("after") or ["0"])[0]))
    except ValueError:
        after = 0
    return moby, after


_BASE = "status = 'complete' AND omeka_item_id = ANY(%s)"


def queue_counts(connect: ConnectFn, hidden: set[int]) -> dict[str, int]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT moby_status, count(*) FROM game_ingest WHERE {_BASE} GROUP BY moby_status",
                (sorted(hidden),),
            )
            rows = cur.fetchall()
    counts = {(m or "(none)"): int(n) for m, n in rows}
    counts[""] = sum(counts.values())
    return counts


def next_in_queue(connect: ConnectFn, hidden: set[int], *, moby: str = "", after: int = 0) -> dict[str, Any] | None:
    if not hidden:
        return None
    where = _BASE + (" AND moby_status = %s" if moby else "")
    base_args: tuple = (sorted(hidden),) + ((moby,) if moby else ())
    with connect() as conn:
        with conn.cursor() as cur:
            for start in ((after, 0) if after else (0,)):
                cur.execute(
                    f"SELECT * FROM game_ingest WHERE {where} AND id > %s ORDER BY id LIMIT 1",
                    base_args + (start,),
                )
                row = cur.fetchone()
                if row is not None:
                    return dict(zip([d[0] for d in cur.description], row))
    return None


# --- rendering -------------------------------------------------------------------------------

def _queue_href(moby: str = "", after: int = 0) -> str:
    params = {k: v for k, v in (("moby", moby), ("after", str(after) if after else "")) if v}
    return "/review" + (f"?{urlencode(params)}" if params else "")


def _chips(counts: dict[str, int], moby: str) -> str:
    out = []
    for key, label in (("", "all hidden"), *MOBY_FILTERS.items()):
        cls = "chip chip-active" if key == moby else "chip"
        out.append(
            f'<a class="{cls}" href="{html.escape(_queue_href(key))}"><strong>{counts.get(key, 0)}</strong>'
            f"{html.escape(label)}</a>"
        )
    return f'<div class="summary">{"".join(out)}</div>'


_INTRO = (
    "<p>Complete packages whose catalog record is still hidden, oldest first. Check each record, "
    "then publish it, or correct or quarantine it first. Skipped records come round again.</p>"
)


def _value(text: str) -> str:
    return html.escape(text) if text else '<span class="muted">—</span>'


def render_review_page(
    *,
    row: dict[str, Any],
    item: dict | None,
    counts: dict[str, int],
    moby: str,
    status_config: dict,
    admin_base: str,
    site_base: str,
) -> bytes:
    rid = int(row["id"])
    item_id = int(row["omeka_item_id"])
    key = str(row.get("game_key") or "")
    here = _queue_href(moby, rid - 1)  # come back to this record after correcting it
    back = urlencode({"omeka_ref": str(item_id), "return_to": here})
    admin = html.escape(f"{admin_base}/item/{item_id}/show")
    public = html.escape(f"{site_base}/item/{item_id}")  # hidden items show there when signed in to Omeka
    if item is not None:
        cur = current_values(item)
        desc = cur["description"]
        if len(desc) > DESCRIPTION_MAX:
            desc = desc[:DESCRIPTION_MAX] + "…"
        moby_link = (
            f'<a href="{html.escape(cur["moby_url"])}" target="_blank" rel="noopener">'
            f"{html.escape(cur['moby_url'])} ↗</a>"
            if cur["moby_url"]
            else '<span class="muted">none</span>'
        )
        title = cur["title"] or key
        details = f"""<table class="kv review-kv"><tbody>
<tr><th scope="row">MobyGames</th><td>{moby_badge(row.get("moby_status"))} {moby_link}
{f'<div class="muted">Ledger match: {html.escape(str(row["moby_title"]))}</div>' if row.get("moby_title") else ""}</td></tr>
<tr><th scope="row">Developers</th><td>{_value(cur["developer"])}</td></tr>
<tr><th scope="row">Publishers</th><td>{_value(cur["publisher"])}</td></tr>
<tr><th scope="row">Description</th><td><div class="review-desc">{_value(desc)}</div></td></tr>
</tbody></table>"""
    else:
        title = str(row.get("moby_title") or key)
        details = (
            '<div class="notice notice-fail" role="status">Couldn\'t read this record from Omeka just now, '
            "so its current details aren't shown. Open it in the Omeka admin, or skip it and come back.</div>"
        )
    left = counts.get(moby, 0)
    inner = f"""{_INTRO}
{_chips(counts, moby)}
<div class="panel review-card">
<p class="muted">{left} left in this view</p>
<h2>{html.escape(title)}</h2>
<p><a href="{html.escape(item_href(key))}"><code>{html.escape(key)}</code></a> · {status_badge(row.get("status"))}
 · Omeka item {item_id} · <a href="{public}" target="_blank" rel="noopener">Public page ↗</a>
 · <a href="{admin}" target="_blank" rel="noopener">Omeka admin ↗</a></p>
{details}
{render_damaged_panel(row, is_public=False)}
<form method="post" action="/review/publish" class="actions review-actions">
<input type="hidden" name="omeka_ref" value="{item_id}">
<input type="hidden" name="moby" value="{html.escape(moby)}">
<input type="hidden" name="after" value="{rid}">
<button type="submit" data-busy="Publishing…" accesskey="p">Publish and next</button>
<a class="btn btn-secondary" href="{html.escape(_queue_href(moby, rid))}" accesskey="s">Skip</a>
<a href="{html.escape('/correct?' + back)}">Correct…</a>
<a class="link-danger" href="{html.escape('/quarantine?' + back)}">Quarantine…</a>
</form>
</div>"""
    return render_page_shell(title="Review hidden records", active="review", inner=inner, status_config=status_config)


def render_review_empty(*, counts: dict[str, int], moby: str, status_config: dict) -> bytes:
    inner = f"""{_INTRO}
{_chips(counts, moby)}
<div class="panel"><p>Nothing left to review in this view.</p></div>"""
    return render_page_shell(title="Review hidden records", active="review", inner=inner, status_config=status_config)


def render_review_unavailable(*, status_config: dict) -> bytes:
    inner = (
        _INTRO
        + '<div class="notice notice-fail" role="status">Couldn\'t get the list of hidden items from Omeka. '
        "Try again in a minute; if it keeps failing, ask an admin.</div>"
    )
    return render_page_shell(title="Review hidden records", active="review", inner=inner, status_config=status_config)
