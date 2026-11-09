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
    assert lhr.FLAG_CONDITIONS["damaged"][0] in lhr.DAMAGED_COUNT_SQL


def _matching(flag: str) -> set[str]:
    """Run a flag's SQL condition against sample rows (plain SQL, so SQLite can run it)."""
    import sqlite3
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE game_ingest (game_key TEXT, status TEXT, omeka_item_id INT, error_message TEXT)")
    db.executemany("INSERT INTO game_ingest VALUES (?, ?, ?, ?)", [
        ("ok", "complete", 1, None),
        ("failed", "failed", None, "boom"),
        ("no-record", "complete", None, None),
        ("held", "complete", None, "held for review: catalogued by hand"),
        ("error", "complete", 2, "upload retried"),
        ("quarantined", "quarantine", 3, "promo disc, not for the public"),
        ("quarantined-no-record", "quarantine", None, "duplicate"),
        ("running", "running", None, ""),
    ])
    cond = lhr.FLAG_CONDITIONS[flag][0]
    return {r[0] for r in db.execute(f"SELECT game_key FROM game_ingest WHERE {cond}")}


def test_quarantined_packages_do_not_need_attention() -> None:
    # A quarantine is a decision already made; its note lives in error_message.
    assert _matching("attention") == {"failed", "no-record", "held", "error"}


def test_quarantine_notes_are_not_counted_as_errors() -> None:
    assert _matching("failed_or_error") == {"failed", "held", "error"}
