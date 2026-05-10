"""Alpha Vantage 429 must NOT call time.sleep — it should log and return [].
The screener worker can retry on the next 20-minute cycle."""
from unittest.mock import patch, MagicMock

import pytest

from app.services.alpha_vantage_service import AlphaVantageService


def _make_429_response(text="rate limit exceeded"):
    resp = MagicMock()
    resp.status_code = 429
    resp.text = text
    resp.json.return_value = {}
    return resp


def test_get_company_news_returns_empty_on_429_without_sleeping():
    av = AlphaVantageService()
    av.api_key = "stub"
    fake_resp = _make_429_response()

    with patch("app.services.alpha_vantage_service.requests.get", return_value=fake_resp) as req, \
         patch("app.services.alpha_vantage_service.time.sleep") as sleep_spy:
        result = av.get_company_news("AAPL", start_date="2026-05-01", end_date="2026-05-08")

    assert result == []
    sleep_spy.assert_not_called()
    # Must NOT have retried the request
    assert req.call_count == 1


def test_get_company_news_returns_empty_on_rate_limit_text_in_200():
    """Alpha Vantage sometimes returns 200 with body text 'rate limit'."""
    av = AlphaVantageService()
    av.api_key = "stub"
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "Information: Our standard API rate limit is 25 requests per day"
    resp.json.return_value = {"Information": "rate limit"}

    with patch("app.services.alpha_vantage_service.requests.get", return_value=resp) as req, \
         patch("app.services.alpha_vantage_service.time.sleep") as sleep_spy:
        result = av.get_company_news("AAPL", start_date="2026-05-01", end_date="2026-05-08")

    assert result == []
    sleep_spy.assert_not_called()
    assert req.call_count == 1
