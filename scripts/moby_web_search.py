#!/usr/bin/env python3
"""Fallback Moby game ID discovery via DuckDuckGo (when API title search returns nothing)."""
from __future__ import annotations

import re
from typing import Any

_MOBY_GAME_ID_LOOSE = re.compile(r"mobygames\.com/game/(\d+)", re.I)


def moby_game_id_from_url(url: str) -> int | None:
    match = _MOBY_GAME_ID_LOOSE.search(url or "")
    return int(match.group(1)) if match else None


def _ddg_text_results(query: str, *, max_results: int) -> list[dict[str, Any]]:
    from ddgs import DDGS

    return list(DDGS().text(query, max_results=max_results))


def duckduckgo_moby_game_id(
    search_title: str,
    *,
    max_results: int = 10,
) -> tuple[int | None, str | None]:
    """
    Search DuckDuckGo for ``{title} mobygames`` and return the first Moby game id.

    Uses the ``ddgs`` library only (no html.duckduckgo.com scraping).
    """
    query = f"{search_title.strip()} mobygames"
    if not query.strip():
        return None, None
    try:
        rows = _ddg_text_results(query, max_results=max_results)
    except Exception:
        return None, None
    seen: set[int] = set()
    for row in rows:
        href = str(row.get("href") or row.get("link") or "")
        game_id = moby_game_id_from_url(href)
        if game_id is None or game_id in seen:
            continue
        seen.add(game_id)
        return game_id, f"https://www.mobygames.com/game/{game_id}/"
    return None, None
