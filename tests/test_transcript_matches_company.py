"""Suffix-normalization tests for the DefeatBeta company-name guard.

Real-world failures these guard against:
- MP Materials Corp.  (2026-05-14)
- Gap, Inc. (The)     (prior session)
- Vodafone Group Plc  (prior session)
"""

import os
import sys

os.environ.setdefault("DB_PATH", "test_match.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services.stock_service import StockService


cases = [
    # (transcript_head, expected_company)
    ("Welcome to the MP Materials earnings call.", "MP Materials Corp."),
    ("Welcome everyone to The Gap's quarterly conference call.", "Gap, Inc. (The)"),
    ("Vodafone reported third-quarter results today.", "Vodafone Group Plc"),
    ("Loews Corporation third-quarter conference call.", "Loews Corporation"),
    ("Loblaw Companies Limited results.", "Loblaw Companies Limited"),
]


@pytest.mark.parametrize("transcript_head, company", cases)
def test_normalization_accepts_known_suffix_variants(transcript_head, company):
    transcript_text = transcript_head + " " + ("x" * 500)
    assert StockService._transcript_matches_company(transcript_text, company), (
        f"expected to match: company={company!r}"
    )


def test_normalization_still_rejects_wrong_company():
    # The classic L -> Loblaw collision should still be rejected when the
    # expected company is Loews.
    transcript = "Welcome to the Loblaw Companies earnings call. " + "x" * 500
    assert not StockService._transcript_matches_company(transcript, "Loews Corporation")


def test_short_name_after_suffix_strip_still_matches():
    """'XP Inc.' strips to 'XP' (2 chars) under suffix normalization, which
    is below the 3-char disambiguation floor. The short-name fallback must
    still match it against the transcript head rather than false-rejecting."""
    transcript = "Welcome to the XP Inc. fourth-quarter earnings call. " + "x" * 500
    assert StockService._transcript_matches_company(transcript, "XP Inc.")


def test_short_name_fallback_still_rejects_wrong_company():
    """The short-name fallback must not turn into a blanket accept — a
    transcript for a different company is still rejected."""
    transcript = "Welcome to the Apple Inc. earnings call. " + "x" * 500
    assert not StockService._transcript_matches_company(transcript, "XP Inc.")


def test_normalization_rejects_degenerate_parenthetical_only_input():
    """If the expected_company strips to empty (or near-empty) after
    normalization, fall through to AV rather than accept any transcript."""
    transcript = "Apple Inc. earnings call transcript. " + "x" * 500
    assert not StockService._transcript_matches_company(transcript, "(The)")
