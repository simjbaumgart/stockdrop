from datetime import date
from unittest.mock import patch, MagicMock

import pytest

from app.services.alpha_vantage_service import AlphaVantageService


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "fake-key")
    s = AlphaVantageService()
    # Force-reset the class-level counter for test isolation
    AlphaVantageService._reset_daily_counter_for_test()
    return s


def _mock_response(json_body, status=200):
    m = MagicMock()
    m.json.return_value = json_body
    m.status_code = status
    return m


def test_success_returns_flattened_text(svc):
    body = {
        "symbol": "AAPL",
        "quarter": "2026Q1",
        "transcript": [
            {"speaker": "Tim Cook", "title": "CEO", "content": "Good afternoon."},
            {"speaker": "Kevan Parekh", "title": "CFO", "content": "Revenue was $143B."},
        ],
    }
    with patch("app.services.alpha_vantage_service.requests.get",
               return_value=_mock_response(body)):
        result = svc.get_earnings_call_transcript("AAPL", "2026Q1")
    assert result["text"].startswith("Good afternoon.") or "Good afternoon." in result["text"]
    assert "Revenue was $143B." in result["text"]
    assert result["segment_count"] == 2


def test_empty_transcript_returns_empty_text(svc):
    body = {"symbol": "AAPL", "quarter": "2026Q1", "transcript": []}
    with patch("app.services.alpha_vantage_service.requests.get",
               return_value=_mock_response(body)):
        result = svc.get_earnings_call_transcript("AAPL", "2026Q1")
    assert result["text"] == ""
    assert result["segment_count"] == 0


def test_rate_limit_response_returns_empty(svc):
    body = {"Information": "We have detected your API key... 25 requests per day."}
    with patch("app.services.alpha_vantage_service.requests.get",
               return_value=_mock_response(body)):
        result = svc.get_earnings_call_transcript("AAPL", "2026Q1")
    assert result["text"] == ""
    assert result["rate_limited"] is True


def test_daily_counter_increments_on_attempted_call(svc):
    body = {"symbol": "AAPL", "quarter": "2026Q1", "transcript": []}
    with patch("app.services.alpha_vantage_service.requests.get",
               return_value=_mock_response(body)) as mocked:
        svc.get_earnings_call_transcript("AAPL", "2026Q1")
        svc.get_earnings_call_transcript("AAPL", "2025Q4")
    assert AlphaVantageService._daily_call_count == 2
    assert mocked.call_count == 2


def test_daily_cap_skips_call(svc):
    """When counter is at AV_TRANSCRIPT_DAILY_CAP, no HTTP call is made."""
    AlphaVantageService._daily_call_count = AlphaVantageService.AV_TRANSCRIPT_DAILY_CAP
    AlphaVantageService._counter_date = date.today()
    with patch("app.services.alpha_vantage_service.requests.get") as mocked:
        result = svc.get_earnings_call_transcript("AAPL", "2026Q1")
    assert mocked.call_count == 0
    assert result["text"] == ""
    assert result["quota_exhausted"] is True


def test_counter_resets_at_new_utc_day(svc):
    """If recorded date != today, counter resets to 0 before incrementing."""
    from datetime import timedelta
    AlphaVantageService._daily_call_count = AlphaVantageService.AV_TRANSCRIPT_DAILY_CAP
    AlphaVantageService._counter_date = date.today() - timedelta(days=1)
    body = {"symbol": "AAPL", "quarter": "2026Q1", "transcript": []}
    with patch("app.services.alpha_vantage_service.requests.get",
               return_value=_mock_response(body)) as mocked:
        svc.get_earnings_call_transcript("AAPL", "2026Q1")
    assert mocked.call_count == 1
    assert AlphaVantageService._daily_call_count == 1
    assert AlphaVantageService._counter_date == date.today()


def test_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    s = AlphaVantageService()
    result = s.get_earnings_call_transcript("AAPL", "2026Q1")
    assert result["text"] == ""


def test_network_error_returns_empty(svc):
    with patch("app.services.alpha_vantage_service.requests.get",
               side_effect=ConnectionError("boom")):
        result = svc.get_earnings_call_transcript("AAPL", "2026Q1")
    assert result["text"] == ""
