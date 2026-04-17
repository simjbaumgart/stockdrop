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
