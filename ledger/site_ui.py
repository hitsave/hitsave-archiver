"""Shared HitSave Ledger chrome (nav + page wrapper)."""
from __future__ import annotations

import html
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

_THEME_PATH = Path(__file__).resolve().parent / "preserve_theme.css"
_REQUEST = threading.local()


def set_current_user(user: str) -> None:
    """Signed-in user for the request being handled on this thread ('' when unknown)."""
    _REQUEST.user = user or ""


def current_user() -> str:
    return getattr(_REQUEST, "user", "")


def set_current_notice(notice_html: str) -> None:
    """Already-rendered notice (notice.render_notice) for the request on this thread."""
    _REQUEST.notice = notice_html or ""


def current_notice() -> str:
    return getattr(_REQUEST, "notice", "")


# Optional enhancement: a submit button with data-busy shows that text and can't be clicked twice
# while the server runs the action. Forms still work without JavaScript.
_BUSY_SCRIPT = """<script>
document.addEventListener("submit", function (e) {
  if (e.defaultPrevented) return;
  var b = e.target.querySelector("button[data-busy]");
  if (!b) return;
  if (b.disabled) { e.preventDefault(); return; }
  setTimeout(function () { b.disabled = true; b.textContent = b.dataset.busy; }, 0);
});
</script>"""


@lru_cache(maxsize=1)
def _theme_css() -> str:
    if _THEME_PATH.is_file():
        return _THEME_PATH.read_text(encoding="utf-8")
    return ""


def _generated_dir(status_config: dict) -> str:
    logs = status_config.get("logs") or {}
    gen = (logs.get("generated_dir") or "/archiver/.generated").strip()
    return gen or "/archiver/.generated"


def render_nav(*, active: str, status_config: dict) -> str:
    links: list[tuple[str, str, str]] = [
        ("ledger", "/", "Ledger"),
        ("review", "/review", "Review"),
        ("disc", "/disc-contents", "Disc contents"),
        ("health", "/health", "Health"),
        ("correct", "/correct", "Catalog correction"),
        ("quarantine", "/quarantine", "Quarantine"),
        ("ops", "/ops", "Windmill runs"),
        ("logs", "/logs", "Batch logs"),
    ]
    nav_links: list[str] = []
    if not Path(_generated_dir(status_config)).is_dir():
        # Batch logs live on the host that runs the batches; hide the page where they aren't mounted.
        links = [link for link in links if link[0] != "logs"]
    for key, href, label in links:
        if key == active:
            nav_links.append(f'<a href="{href}" class="active">{html.escape(label)}</a>')
        else:
            nav_links.append(f'<a href="{href}">{html.escape(label)}</a>')

    extra = ""
    ops = status_config.get("ops") or {}
    windmill = (ops.get("windmill_ui_url") or "").strip().rstrip("/")
    if windmill:
        extra = (
            f'<span class="nav-extra">'
            f'<a href="{html.escape(windmill)}" target="_blank" rel="noopener">Windmill UI ↗</a>'
            f"</span>"
        )

    user = current_user()
    if user:
        extra += (
            f'<span class="nav-extra">Signed in as {html.escape(user)} · '
            f'<a href="/oauth2/sign_out">Sign out</a></span>'
        )

    return f'<nav class="site-nav">{"".join(nav_links)}{extra}</nav>'


def render_page_shell(*, title: str, active: str, inner: str, status_config: dict) -> bytes:
    nav = render_nav(active=active, status_config=status_config)
    gen = html.escape(_generated_dir(status_config))
    css = _theme_css()
    doc = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · HitSave Ledger</title>
<style>{css}</style>
</head><body>
<header class="site-header">
<div class="brand-stripe" aria-hidden="true"></div>
<div class="header-inner">
<div class="family-bar">
<a class="brand-mark" href="/">HitSave Ledger</a>
<span class="muted">·</span>
<a href="https://preserve.games/" target="_blank" rel="noopener">preserve.games</a>
<span class="muted">·</span>
<a href="https://archive.hitsave.org/" target="_blank" rel="noopener">archive.hitsave.org</a>
</div>
{nav}
</div>
</header>
<main class="page">
<h1>{html.escape(title)}</h1>
{current_notice()}
{inner}
</main>
<footer class="site-footer">
<p>{f'Batch logs: <code>{gen}</code> · ' if Path(_generated_dir(status_config)).is_dir() else ''}MobyGames fields where shown: Data by MobyGames.com</p>
</footer>
{_BUSY_SCRIPT}
</body></html>"""
    return doc.encode("utf-8")
