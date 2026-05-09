"""Pre-fetch structured EPS facts from Finnhub. The PM must see actual,
consensus, and surprise % as a single dict — not a free-form LLM summary
of news articles (the TOST $0.20 vs $0.15 vs actual $0.27 incident)."""
from unittest.mock import patch, MagicMock

import pytest

from app.services.finnhub_service import FinnhubService


def test_get_earnings_facts_returns_latest_period():
    fh = FinnhubService.__new__(FinnhubService)
    fh.client = MagicMock()
    fh.client.company_earnings.return_value = [
        {"actual": 0.27, "estimate": 0.20, "period": "2026-03-31",
         "quarter": 1, "surprise": 0.07, "surprisePercent": 35.0,
         "symbol": "TOST", "year": 2026},
        {"actual": 0.18, "estimate": 0.21, "period": "2025-12-31",
         "quarter": 4, "surprise": -0.03, "surprisePercent": -14.3,
         "symbol": "TOST", "year": 2025},
    ]
    facts = fh.get_earnings_facts("TOST")

    assert facts["reported_eps"] == 0.27
    assert facts["consensus_eps"] == 0.20
    assert facts["surprise_pct"] == 35.0
    assert facts["fiscal_quarter"] == "2026Q1"
    assert facts["period"] == "2026-03-31"
    assert facts["fetched_at"]  # ISO 8601 string


def test_get_earnings_facts_returns_none_when_no_data():
    fh = FinnhubService.__new__(FinnhubService)
    fh.client = MagicMock()
    fh.client.company_earnings.return_value = []
    assert fh.get_earnings_facts("XYZ") is None


def test_get_earnings_facts_handles_partial_rows():
    """If actual or estimate is missing, return None rather than emit garbage."""
    fh = FinnhubService.__new__(FinnhubService)
    fh.client = MagicMock()
    fh.client.company_earnings.return_value = [
        {"period": "2026-03-31", "quarter": 1, "year": 2026, "symbol": "ABC",
         "actual": None, "estimate": 0.20},
    ]
    assert fh.get_earnings_facts("ABC") is None


def test_get_earnings_facts_handles_no_client():
    fh = FinnhubService.__new__(FinnhubService)
    fh.client = None
    assert fh.get_earnings_facts("ANY") is None
