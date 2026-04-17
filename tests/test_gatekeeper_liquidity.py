import pytest
from app.services.gatekeeper_service import GatekeeperService, MIN_PRICE_USD


@pytest.fixture
def gatekeeper():
    return GatekeeperService()


def test_liquidity_filter_rejects_penny_stock(gatekeeper):
    is_ok, reason = gatekeeper.check_liquidity_filter(price=0.003)
    assert is_ok is False
    assert "price" in reason.lower()
    assert "0.00" in reason or "0.003" in reason


def test_liquidity_filter_rejects_just_under_threshold(gatekeeper):
    is_ok, reason = gatekeeper.check_liquidity_filter(price=4.99)
    assert is_ok is False
    assert "4.99" in reason


def test_liquidity_filter_accepts_at_threshold(gatekeeper):
    is_ok, reason = gatekeeper.check_liquidity_filter(price=MIN_PRICE_USD)
    assert is_ok is True


def test_liquidity_filter_accepts_above_threshold(gatekeeper):
    is_ok, reason = gatekeeper.check_liquidity_filter(price=42.0)
    assert is_ok is True


def test_liquidity_filter_rejects_zero_or_missing_price(gatekeeper):
    is_ok, reason = gatekeeper.check_liquidity_filter(price=0.0)
    assert is_ok is False


def test_min_price_constant_is_five_dollars():
    assert MIN_PRICE_USD == 5.0


def test_check_technical_filters_rejects_sub_5_penny_stock_via_cached(gatekeeper):
    # PBMRF-like: price way under $5, but Bollinger math would otherwise pass
    cached = {
        "close": 0.003,
        "bb_lower": 0.001,
        "bb_upper": 0.010,
        "volume": 1_000_000,
    }
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="PBMRF", cached_indicators=cached
    )
    assert is_valid is False
    assert "liquidity_status" in reasons
    assert "Price" in reasons["liquidity_status"]
    # Ensure we short-circuited: no Bollinger verdict should be produced
    assert "bb_status" not in reasons


def test_check_technical_filters_accepts_above_5_with_dip(gatekeeper):
    cached = {
        "close": 42.00,
        "bb_lower": 40.00,
        "bb_upper": 60.00,
        "volume": 2_000_000,
    }
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="AAPL", cached_indicators=cached
    )
    # %B = (42-40)/(60-40) = 0.10 -> qualifies
    assert is_valid is True
    assert "liquidity_status" in reasons
    assert "bb_status" in reasons


def test_check_technical_filters_rejects_above_5_when_not_dipped(gatekeeper):
    cached = {
        "close": 50.00,
        "bb_lower": 40.00,
        "bb_upper": 60.00,
        "volume": 2_000_000,
    }
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="MSFT", cached_indicators=cached
    )
    # %B = 0.5, which is NOT < 0.50 -> reject
    assert is_valid is False
    assert "liquidity_status" in reasons  # liquidity passed
    assert "bb_status" in reasons  # but BB rejected
