# tests/test_fm_semantic_check_factor_count.py
"""v0.8.2-288 review #2 (AAOI): Flash repair of a truncated PM answer
returned 2 of 3 key_factors with mangled heads — and passed the semantic
check. The PM prompt mandates exactly 3 factors; a present-but-short list
is the truncation signature and must fail (-> triggers the re-prompt path)."""

from app.services.research_service import _fm_semantic_check

BASE = {"action": "BUY", "conviction": "HIGH"}


def test_two_factors_fail():
    ok, reason = _fm_semantic_check({**BASE, "key_factors": [
        "con firms the May 2026 $600M ATM filing remains an overhang",
        "Technical support held at the 50-day SMA",
    ]})
    assert not ok
    assert "key_factors" in reason


def test_three_factors_pass():
    ok, _ = _fm_semantic_check({**BASE, "key_factors": [
        "Earnings beat by 5.4% per canonical Finnhub facts",
        "Sector peers down in sympathy, attribution SECTOR",
        "Falling-knife flag NO from risk agent",
    ]})
    assert ok


def test_missing_or_empty_factors_still_tolerated():
    # Honest truncation BEFORE the key_factors field repairs to None/[] —
    # that remains acceptable (NIO 2026-05-22 precedent).
    assert _fm_semantic_check({**BASE})[0]
    assert _fm_semantic_check({**BASE, "key_factors": []})[0]
