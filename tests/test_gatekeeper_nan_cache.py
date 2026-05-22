"""BB-NaN rejection caching.

Recently-IPO'd tickers (CBRS, FRVO 2026-05-22) lack enough price history for
the 20-day Bollinger window, so %B is NaN and the gatekeeper rejects them.
Without a cache they reappear every 20-minute screener cycle and get the
full technical-indicator fetch re-run. The gatekeeper caches the BB-NaN
rejection with a 24h TTL so they are short-circuited until the next day,
by which point the IPO may have accumulated enough history.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from app.services.gatekeeper_service import GatekeeperService

NAN_INDICATORS = {
    "close": 100.0,
    "bb_lower": float("nan"),
    "bb_upper": float("nan"),
    "average_volume_10d": 5_000_000.0,
}
# price 90, lower 95, upper 105 -> %B = -0.5 (Deep Dip, valid)
VALID_INDICATORS = {
    "close": 90.0,
    "bb_lower": 95.0,
    "bb_upper": 105.0,
    "average_volume_10d": 5_000_000.0,
}


def _check(gk, symbol, indicators):
    with patch(
        "app.services.gatekeeper_service.tradingview_service.get_technical_indicators",
        return_value=indicators,
    ) as mock_fetch:
        is_valid, reasons = gk.check_technical_filters(symbol=symbol, drop_pct=-12.0)
    return is_valid, reasons, mock_fetch


def test_bb_nan_rejection_is_cached():
    gk = GatekeeperService()
    _check(gk, "CBRS", NAN_INDICATORS)
    assert "CBRS" in gk._bb_nan_cache


def test_cached_ticker_short_circuits_without_fetch():
    gk = GatekeeperService()
    _check(gk, "FRVO", NAN_INDICATORS)  # first call: rejects + caches
    is_valid, reasons, mock_fetch = _check(gk, "FRVO", NAN_INDICATORS)  # second
    assert is_valid is False
    assert "nan" in reasons.get("bb_status", "").lower()
    mock_fetch.assert_not_called()  # expensive indicator fetch is skipped


def test_expired_cache_entry_triggers_refetch():
    gk = GatekeeperService()
    gk._bb_nan_cache["CBRS"] = datetime.now() - timedelta(seconds=1)  # expired
    is_valid, reasons, mock_fetch = _check(gk, "CBRS", VALID_INDICATORS)
    mock_fetch.assert_called_once()  # stale entry ignored — re-evaluated
    assert is_valid is True  # IPO now has enough history


def test_valid_ticker_is_not_cached():
    gk = GatekeeperService()
    _check(gk, "AAPL", VALID_INDICATORS)
    assert "AAPL" not in gk._bb_nan_cache
