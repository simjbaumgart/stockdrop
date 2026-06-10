"""Unit tests for BenzingaService circuit breaker + market-news cache.

These exercise the resilience logic added to stop a dead Polygon news endpoint
from stalling every candidate (~10s timeout * 4 calls/stock). No network: requests
is mocked.
"""
from unittest.mock import patch, MagicMock

from app.services.benzinga_service import BenzingaService


def _svc():
    svc = BenzingaService()
    svc.api_key = "test-key"  # bypass env lookup
    return svc


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload or {"results": []}
    return r


def test_breaker_trips_after_consecutive_failures_then_short_circuits():
    svc = _svc()
    with patch("app.services.benzinga_service.requests.get", side_effect=Exception("timeout")) as mock_get:
        # FAILURES_TO_TRIP failures should open the breaker.
        for _ in range(svc.FAILURES_TO_TRIP):
            assert svc.get_company_news("AAPL") == []
        assert svc._breaker_open() is True
        calls_after_trip = mock_get.call_count

        # Subsequent calls must NOT hit the network while the breaker is open.
        assert svc.get_company_news("MSFT") == []
        assert svc.get_company_news("NVDA") == []
        assert mock_get.call_count == calls_after_trip


def test_non_200_counts_as_failure():
    svc = _svc()
    with patch("app.services.benzinga_service.requests.get", return_value=_resp(status=503)):
        for _ in range(svc.FAILURES_TO_TRIP):
            svc.get_company_news("AAPL")
        assert svc._breaker_open() is True


def test_success_resets_failure_counter():
    svc = _svc()
    with patch("app.services.benzinga_service.requests.get") as mock_get:
        # Two failures, then a success — counter must reset so the breaker stays closed.
        mock_get.side_effect = [Exception("x"), Exception("x"), _resp(status=200)]
        svc.get_company_news("AAPL")
        svc.get_company_news("AAPL")
        svc.get_company_news("AAPL")
        assert svc._consecutive_failures == 0
        assert svc._breaker_open() is False


def test_breaker_reopens_after_cooldown():
    svc = _svc()
    with patch("app.services.benzinga_service.requests.get", side_effect=Exception("timeout")):
        for _ in range(svc.FAILURES_TO_TRIP):
            svc.get_company_news("AAPL")
        assert svc._breaker_open() is True
        # Simulate cooldown elapsing.
        svc._disabled_until = 1.0  # far in the past
        assert svc._breaker_open() is False  # half-open, allows probing


def test_market_news_is_cached_within_ttl():
    svc = _svc()
    payload = {"results": [{"title": "Headline A", "published_utc": "2026-05-01T00:00:00Z"}]}
    with patch("app.services.benzinga_service.requests.get", return_value=_resp(payload=payload)) as mock_get:
        first = svc.get_market_news(limit=5)
        calls_after_first = mock_get.call_count
        # 3 ETF tickers => 3 fetches on a cold cache.
        assert calls_after_first == len(svc.MARKET_TICKERS)

        second = svc.get_market_news(limit=5)
        # Cached: no additional network calls.
        assert mock_get.call_count == calls_after_first
        assert second == first
