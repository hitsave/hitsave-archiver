"""One-line notices shown after a redirect (\"Item 1234 is now public.\"), and safe redirect targets."""
from __future__ import annotations

import html
from urllib.parse import parse_qsl, urlencode, urlparse

KINDS = ("ok", "fail")
MESSAGE_MAX = 300


def _is_local(path: str) -> bool:
    return path.startswith("/") and not path.startswith("//") and "\\" not in path


def safe_return_path(return_to: str, referer: str) -> str:
    """Where to send the browser after a POST: a local path only, never another site."""
    back = (return_to or "").strip()
    if _is_local(back):
        return back
    ref_path = urlparse((referer or "").strip()).path if referer else ""
    return ref_path if _is_local(ref_path) else "/"


def with_notice(path: str, kind: str, message: str) -> str:
    """`path` with notice=<kind>&msg=<message> set (any earlier notice replaced)."""
    parsed = urlparse(path)
    params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k not in ("notice", "msg")]
    params += [("notice", kind), ("msg", message[:MESSAGE_MAX])]
    return f"{parsed.path}?{urlencode(params)}"


def render_notice(qs: dict[str, list[str]]) -> str:
    kind = (qs.get("notice") or [""])[0]
    msg = (qs.get("msg") or [""])[0].strip()[:MESSAGE_MAX]
    if kind not in KINDS or not msg:
        return ""
    return f'<div class="notice notice-{kind}" role="status">{html.escape(msg)}</div>'


def visibility_message(item_ref: str, is_public: bool, code: int, output: str) -> tuple[str, str]:
    if code == 0:
        return "ok", f"Item {item_ref} is now {'public' if is_public else 'private'}."
    lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
    reason = lines[-1] if lines else "no output from the visibility script."
    verb = "publish" if is_public else "hide"
    return "fail", f"Couldn't {verb} item {item_ref}: {reason}"
