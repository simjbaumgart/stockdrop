from unittest.mock import patch, MagicMock

import pytest

from app.services.finnhub_service import FinnhubService


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "fake-key")
    return FinnhubService()


def _earnings_response(rows):
    """Mimic finnhub-python client.company_earnings() return shape: a list of dicts."""
    return rows


def test_returns_latest_quarter_string(svc):
    rows = [
        {"period": "2025-09-30", "quarter": 3, "year": 2025, "actual": 1.2, "estimate": 1.0,
         "surprise": 0.2, "surprisePercent": 20.0, "symbol": "AAPL"},
        {"period": "2025-12-31", "quarter": 1, "year": 2026, "actual": 2.84, "estimate": 2.72,
         "surprise": 0.12, "surprisePercent": 4.4, "symbol": "AAPL"},
    ]
    with patch.object(svc.client, "company_earnings", return_value=_earnings_response(rows)):
        q = svc.get_latest_reported_quarter("AAPL")
    # Most recent period is 2025-12-31 — Finnhub already labels it as quarter=1, year=2026
    assert q == "2026Q1"


def test_returns_none_on_empty(svc):
    with patch.object(svc.client, "company_earnings", return_value=[]):
        assert svc.get_latest_reported_quarter("AAPL") is None


def test_returns_none_on_exception(svc):
    with patch.object(svc.client, "company_earnings", side_effect=RuntimeError("boom")):
        assert svc.get_latest_reported_quarter("AAPL") is None


def test_no_client_returns_none(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    svc = FinnhubService()
    assert svc.client is None
    assert svc.get_latest_reported_quarter("AAPL") is None


def test_picks_max_period_not_first_row(svc):
    """Finnhub may return rows in any order — we must sort by period."""
    rows = [
        {"period": "2025-12-31", "quarter": 1, "year": 2026, "symbol": "AAPL"},
        {"period": "2025-06-30", "quarter": 3, "year": 2025, "symbol": "AAPL"},
        {"period": "2025-09-30", "quarter": 4, "year": 2025, "symbol": "AAPL"},
    ]
    with patch.object(svc.client, "company_earnings", return_value=rows):
        assert svc.get_latest_reported_quarter("AAPL") == "2026Q1"
