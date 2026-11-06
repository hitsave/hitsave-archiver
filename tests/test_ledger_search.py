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


# Filters shared by the Health chips, the attention queue and the Ledger page.

def test_where_adds_status_filter_after_search_terms() -> None:
    cond, binds = lhr.ledger_where("famitsu", status="complete")
    assert "status = %s" in cond
    assert binds == ("% famitsu%", "complete")


def test_where_adds_known_flag_condition() -> None:
    cond, binds = lhr.ledger_where("", flag="no_omeka")
    assert "omeka_item_id IS NULL" in cond
    assert binds == ()


def test_where_ignores_unknown_flag_and_empty_status() -> None:
    assert lhr.ledger_where("", status="", flag="bogus") == ("TRUE", ())


def test_where_combines_everything_in_order() -> None:
    cond, binds = lhr.ledger_where("a b", status="failed", flag="attention")
    assert binds == ("% a%", "% b%", "failed")
    assert lhr.FLAG_CONDITIONS["attention"][0] in cond


def test_health_queries_share_the_flag_conditions() -> None:
    assert lhr.FLAG_CONDITIONS["attention"][0] in lhr.ATTENTION_SELECT
    assert lhr.FLAG_CONDITIONS["failed_or_error"][0] in lhr.FAILED_OR_ERROR_SQL
    assert lhr.FLAG_CONDITIONS["no_omeka"][0] in lhr.COMPLETE_MISSING_OMEKA_SQL
