#!/usr/bin/env python3
"""Human-readable titles from press-material folder names (underscores → spaces, etc.)."""
from __future__ import annotations

import re

TITLE_SUFFIX_SEP = " — "


def normalize_name_fragment(name: str) -> str:
    """Turn folder-style names into readable phrases."""
    text = name.strip().replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_display_title(title: str) -> str:
    """
    Normalize a full Omeka item title, preserving an em-dash suffix (batch labels, pilots).
    Example: ``Total_Immersion_Racing — batch note`` → ``Total Immersion Racing — batch note``.
    """
    title = title.strip()
    if not title:
        return title
    if TITLE_SUFFIX_SEP in title:
        main, suffix = title.split(TITLE_SUFFIX_SEP, 1)
        main = normalize_name_fragment(main)
        suffix = suffix.strip()
        if suffix:
            return f"{main}{TITLE_SUFFIX_SEP}{suffix}"
        return main
    return normalize_name_fragment(title)
