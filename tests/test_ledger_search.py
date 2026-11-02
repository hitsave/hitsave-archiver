"""Ledger word search: every word must appear in the row, punctuation and case ignored."""
from __future__ import annotations

import ledger_health_report as lhr


def test_terms_split_on_punctuation_and_lowercase() -> None:
    assert lhr.search_terms("Famitsu Wave DVD 2008-12") == ["famitsu", "wave", "dvd", "2008", "12"]
    assert lhr.search_terms("  dumps/DVD-Video_Coverdiscs ") == ["dumps", "dvd", "video", "coverdiscs"]
    assert lhr.search_terms("--") == []


def test_where_ands_one_like_per_term() -> None:
    cond, binds = lhr.search_where("famitsu 2008")
    assert cond.count(" LIKE %s") == 2 and " AND " in cond
    assert binds == ("% famitsu%", "% 2008%")
    assert "regexp_replace(lower(concat_ws(" in cond and "game_key" in cond and "omeka_item_id::text" in cond


def test_empty_query_matches_everything() -> None:
    assert lhr.search_where("  ") == ("TRUE", ())
