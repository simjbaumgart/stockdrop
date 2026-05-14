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
