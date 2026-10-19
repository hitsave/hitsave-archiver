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


def duckduckgo_moby_game_candidates(
    search_title: str,
    moby_cfg: dict,
    *,
    max_results: int = 10,
) -> list[tuple[int, str]]:
    """Search ddgs for ``{title} mobygames``; return unique Moby game ids in result order."""
    from moby_resolve import moby_public_game_url

    query = f"{search_title.strip()} mobygames"
    if not query.strip():
        return []
    try:
        rows = _ddg_text_results(query, max_results=max_results)
    except Exception:
        return []
    seen: set[int] = set()
    out: list[tuple[int, str]] = []
    for row in rows:
        href = str(row.get("href") or row.get("link") or "")
        game_id = moby_game_id_from_url(href)
        if game_id is None or game_id in seen:
            continue
        seen.add(game_id)
        out.append((game_id, f"{moby_public_game_url(moby_cfg, game_id)}/"))
    return out


def duckduckgo_moby_game_id(
    search_title: str,
    moby_cfg: dict,
    *,
    max_results: int = 10,
) -> tuple[int | None, str | None]:
    """First DDG Moby hit only (legacy); prefer ``duckduckgo_moby_game_candidates`` + title check."""
    candidates = duckduckgo_moby_game_candidates(
        search_title, moby_cfg, max_results=max_results
    )
    if not candidates:
        return None, None
    game_id, url = candidates[0]
    return game_id, url
