"""Unit tests for DividendService.get_dividend_facts (yfinance mocked)."""
import datetime
from unittest.mock import MagicMock, patch

from app.services.dividend_service import DividendService


def _ticker_with(calendar, info):
    t = MagicMock()
    t.calendar = calendar
    t.info = info
    return t


def test_returns_iso_dates_and_amount():
    cal = {
        "Ex-Dividend Date": datetime.date(2026, 5, 18),
        "Dividend Date": datetime.date(2026, 6, 12),
    }
    info = {"lastDividendValue": 1.23}
    with patch("app.services.dividend_service.yf.Ticker", return_value=_ticker_with(cal, info)):
        out = DividendService().get_dividend_facts("BAP")
    assert out["ex_dividend_date"] == "2026-05-18"
    assert out["pay_date"] == "2026-06-12"
    assert out["amount"] == 1.23
    assert out["source"] == "yfinance"
    assert "fetched_at" in out


def test_returns_none_when_no_ex_dividend_date():
    cal = {"Ex-Dividend Date": None, "Dividend Date": datetime.date(2026, 6, 12)}
    with patch("app.services.dividend_service.yf.Ticker", return_value=_ticker_with(cal, {})):
        assert DividendService().get_dividend_facts("XYZ") is None


def test_amount_none_when_info_missing():
    cal = {"Ex-Dividend Date": datetime.date(2026, 5, 18), "Dividend Date": None}
    with patch("app.services.dividend_service.yf.Ticker", return_value=_ticker_with(cal, {})):
        out = DividendService().get_dividend_facts("XYZ")
    assert out["ex_dividend_date"] == "2026-05-18"
    assert out["pay_date"] is None
    assert out["amount"] is None


def test_returns_none_on_exception():
    with patch("app.services.dividend_service.yf.Ticker", side_effect=RuntimeError("boom")):
        assert DividendService().get_dividend_facts("XYZ") is None


def test_returns_none_when_calendar_not_a_dict():
    # Older yfinance versions returned a DataFrame; treat anything non-dict as no data.
    t = MagicMock()
    t.calendar = ["not", "a", "dict"]
    with patch("app.services.dividend_service.yf.Ticker", return_value=t):
        assert DividendService().get_dividend_facts("XYZ") is None
