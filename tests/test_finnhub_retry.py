"""Tests for the Finnhub transient-error retry helper.

Production behavior (post-fix): one retry with 2s backoff on Timeout,
ConnectionError, and FinnhubAPIException with 5xx status. All other
exceptions raise immediately. After all retries, the original exception
is re-raised so the caller's existing try/except can degrade gracefully.
"""
from unittest.mock import patch, MagicMock

import pytest
import requests
import finnhub

from app.services.finnhub_service import FinnhubService, _call_with_retry


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "fake-key")
    return FinnhubService()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Skip the real backoff sleep in retry tests."""
    monkeypatch.setattr("app.services.finnhub_service.time.sleep", lambda *a, **kw: None)


class TestCallWithRetry:
    def test_returns_result_on_first_success(self):
        method = MagicMock(return_value={"ok": True})
        assert _call_with_retry(method, "AAPL") == {"ok": True}
        assert method.call_count == 1

    def test_retries_once_on_timeout_then_succeeds(self):
        method = MagicMock(
            side_effect=[requests.Timeout("slow"), {"ok": True}]
        )
        assert _call_with_retry(method, "AAPL") == {"ok": True}
        assert method.call_count == 2

    def test_retries_once_on_connection_error_then_succeeds(self):
        method = MagicMock(
            side_effect=[requests.ConnectionError("reset"), {"ok": True}]
        )
        assert _call_with_retry(method, "AAPL") == {"ok": True}
        assert method.call_count == 2

    def test_raises_after_two_consecutive_timeouts(self):
        method = MagicMock(side_effect=requests.Timeout("slow"))
        with pytest.raises(requests.Timeout):
            _call_with_retry(method, "AAPL")
        assert method.call_count == 2

    def test_does_not_retry_on_non_transient_exception(self):
        """ValueError is a programming error, not a network blip."""
        method = MagicMock(side_effect=ValueError("bad arg"))
        with pytest.raises(ValueError):
            _call_with_retry(method, "AAPL")
        assert method.call_count == 1

    def test_retries_on_5xx_finnhub_api_exception(self):
        """FinnhubAPIException with status_code 502/503/504 is transient."""
        resp_500 = MagicMock()
        resp_500.status_code = 503
        resp_500.json.return_value = {"error": "service unavailable"}
        err = finnhub.FinnhubAPIException(resp_500)

        method = MagicMock(side_effect=[err, {"ok": True}])
        assert _call_with_retry(method, "AAPL") == {"ok": True}
        assert method.call_count == 2

    def test_does_not_retry_on_4xx_finnhub_api_exception(self):
        """FinnhubAPIException with 401/403/404 is not transient."""
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.json.return_value = {"error": "unauthorized"}
        err = finnhub.FinnhubAPIException(resp_401)

        method = MagicMock(side_effect=err)
        with pytest.raises(finnhub.FinnhubAPIException):
            _call_with_retry(method, "AAPL")
        assert method.call_count == 1


class TestServiceMethodsUseRetry:
    """The four call sites should swallow transient errors after one retry."""

    def test_company_news_returns_empty_on_persistent_timeout(self, svc):
        with patch.object(svc.client, "company_news",
                          side_effect=requests.Timeout("slow")):
            result = svc.get_company_news("AAPL", "2026-01-01", "2026-01-02")
        assert result == []

    def test_company_news_succeeds_after_one_retry(self, svc):
        with patch.object(svc.client, "company_news",
                          side_effect=[requests.Timeout("slow"), [{"id": 1}]]):
            result = svc.get_company_news("AAPL", "2026-01-01", "2026-01-02")
        assert result == [{"id": 1}]

    def test_filings_returns_empty_on_persistent_timeout(self, svc):
        with patch.object(svc.client, "filings",
                          side_effect=requests.Timeout("slow")):
            result = svc.get_filings("AAPL")
        assert result == []

    def test_company_earnings_returns_none_after_retry_exhausted(self, svc):
        with patch.object(svc.client, "company_earnings",
                          side_effect=requests.Timeout("slow")):
            assert svc.get_latest_reported_quarter("AAPL") is None

    def test_insider_sentiment_returns_empty_after_retry(self, svc):
        with patch.object(svc.client, "stock_insider_sentiment",
                          side_effect=requests.Timeout("slow")):
            assert svc.get_insider_sentiment("AAPL", "2026-01-01", "2026-01-02") == {}
