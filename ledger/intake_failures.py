"""Intake failures: packages hitsave-intake rejected before they reached the ledger.

intake moves a package that fails sidecar verification (or download) to
``<HOST_INTAKE>/failed/<slug>/`` with an ``error.txt``. Those never get a ``game_ingest`` row,
so the ledger alone can't show them. the ledger app mounts that folder read-only and lists entries
that are still open: a slug whose ``/data/intake/ready/<slug>`` source is already ``complete``
in the ledger was preserved later and is left out.
"""
from __future__ import annotations

import html
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from formatting import fmt_ts

FAILED_DIR = Path(os.environ.get("HITSAVE_INTAKE_FAILED_DIR", "/data/intake-failed"))
READY_SOURCE_PREFIX = "/data/intake/ready/"
REASON_MAX = 300


@dataclass
class IntakeFailure:
    slug: str
    failed_at: datetime
    reason: str


def scan_failed_dir(failed_dir: Path = FAILED_DIR) -> list[IntakeFailure]:
    """Every failed/<slug>/ entry, newest first. Missing or unreadable folder -> []."""
    if not failed_dir.is_dir():
        return []
    out: list[IntakeFailure] = []
    for entry in failed_dir.iterdir():
        if not entry.is_dir() or entry.is_symlink():
            continue
        err = entry / "error.txt"
        try:
            stat = (err if err.is_file() else entry).stat()
            text = err.read_text(encoding="utf-8", errors="replace").strip() if err.is_file() else ""
        except OSError:
            continue
        reason = (text.splitlines()[0] if text else "(no error.txt)")[:REASON_MAX]
        out.append(IntakeFailure(entry.name, datetime.fromtimestamp(stat.st_mtime, timezone.utc), reason))
    out.sort(key=lambda f: f.failed_at, reverse=True)
    return out


def ledger_rejections(connect: Callable[[], Any]) -> list[IntakeFailure] | None:
    """Rows of the ledger table intake_rejection (mirror of failed/, synced by each intake sweep),
    newest first; None when the table doesn't exist yet."""
    with connect() as conn:
        exists = conn.execute("SELECT to_regclass('intake_rejection') IS NOT NULL").fetchone()[0]
        if not exists:
            return None
        rows = conn.execute(
            "SELECT slug, failed_at, reason FROM intake_rejection ORDER BY failed_at DESC"
        ).fetchall()
    return [IntakeFailure(slug, failed_at, (reason or "")[:REASON_MAX]) for slug, failed_at, reason in rows]


def load_failures(
    connect: Callable[[], Any], failed_dir: Path = FAILED_DIR
) -> tuple[list[IntakeFailure], int, bool]:
    """(open failures, resolved count, available). Reads failed/ when it's mounted here, else the
    ledger table; available is False when neither exists."""
    if failed_dir.is_dir():
        return (*open_failures(connect, failed_dir), True)
    failures = ledger_rejections(connect)
    if failures is None:
        return [], 0, False
    return (*_drop_resolved(connect, failures), True)


def open_failures(connect: Callable[[], Any], failed_dir: Path = FAILED_DIR) -> tuple[list[IntakeFailure], int]:
    """(still-open failures, count resolved since: slug preserved later per the ledger)."""
    return _drop_resolved(connect, scan_failed_dir(failed_dir))


def _ready_slug_candidates(slug: str) -> set[str]:
    """Ready-folder names a failed slug may have been preserved under.

    Early intake runs doubled the lane prefix (``redump-redump-<name>``); the same package was
    then preserved as ``redump-<name>``, so either name resolves the failure.
    """
    names = {slug}
    lane, sep, rest = slug.partition("-")
    while sep and rest.startswith(lane + "-"):
        names.add(rest)
        rest = rest[len(lane) + 1:]
    return names


def _drop_resolved(
    connect: Callable[[], Any], failures: list[IntakeFailure]
) -> tuple[list[IntakeFailure], int]:
    if not failures:
        return [], 0
    candidates = {f.slug: _ready_slug_candidates(f.slug) for f in failures}
    sources = sorted({READY_SOURCE_PREFIX + c for cs in candidates.values() for c in cs})
    with connect() as conn:
        rows = conn.execute(
            "SELECT source_path FROM game_ingest WHERE status = 'complete' AND source_path = ANY(%s)",
            (sources,),
        ).fetchall()
    done = {r[0][len(READY_SOURCE_PREFIX):] for r in rows}
    open_ = [f for f in failures if not (candidates[f.slug] & done)]
    return open_, len(failures) - len(open_)


def render_intake_failures(
    failures: list[IntakeFailure],
    resolved: int,
    *,
    failed_dir: Path = FAILED_DIR,
    available: bool | None = None,
) -> str:
    if available is None:
        available = failed_dir.is_dir()
    if not available:
        return (
            '<p class="muted">Intake failures aren\'t available here: the intake <code>failed/</code> '
            "folder isn't mounted on this host (intake runs elsewhere). Ask an admin, or check the "
            "inbox sweep's job log on the Windmill runs page.</p>"
        )
    intro = (
        "<p>Packages intake rejected before they reached the ledger, usually because a file "
        "didn't match the checksums in its sidecar, or the upload was incomplete. Intake tries "
        "again automatically once the package changes in the inbox (re-upload or fix the sidecar). "
        "The full error is in <code>failed/&lt;package&gt;/error.txt</code> in the intake folder; "
        "ask an admin if the first line isn't enough.</p>"
    )
    note = f'<p class="muted">{resolved} older failure(s) not shown: those packages were preserved later.</p>' if resolved else ""
    if not failures:
        return intro + '<p class="muted">No open intake failures.</p>' + note
    rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(f.slug)}</code></td>"
        f"<td>{html.escape(fmt_ts(f.failed_at))}</td>"
        f'<td class="col-error">{html.escape(f.reason)}</td>'
        "</tr>"
        for f in failures
    )
    table = (
        '<div class="table-wrap"><table><thead><tr><th>Package folder</th><th>Failed at</th>'
        f"<th>Reason</th></tr></thead><tbody>{rows}</tbody></table></div>"
    )
    return intro + table + note
