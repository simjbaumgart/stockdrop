import pytest
from app.services.gatekeeper_service import (
    GatekeeperService,
    MIN_PRICE_USD,
    TIER_DEEP_DIP,
    TIER_STANDARD_DIP,
    TIER_SHALLOW_DIP,
    TIER_REJECT,
)


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
        symbol="AAPL", cached_indicators=cached, drop_pct=-6.0
    )
    # %B = (42-40)/(60-40) = 0.10 -> DEEP_DIP, qualifies
    assert is_valid is True
    assert "liquidity_status" in reasons
    assert "bb_status" in reasons
    assert reasons["tier"] == TIER_DEEP_DIP


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


def test_min_avg_volume_constant_is_100k():
    from app.services.stock_service import MIN_AVG_VOLUME
    assert MIN_AVG_VOLUME == 100_000


def test_classify_tier_deep_dip(gatekeeper):
    assert gatekeeper.classify_tier(pct_b=0.10, drop_pct=-6.0) == TIER_DEEP_DIP
    assert gatekeeper.classify_tier(pct_b=0.29, drop_pct=-5.5) == TIER_DEEP_DIP


def test_classify_tier_standard_dip(gatekeeper):
    assert gatekeeper.classify_tier(pct_b=0.30, drop_pct=-5.5) == TIER_STANDARD_DIP
    assert gatekeeper.classify_tier(pct_b=0.49, drop_pct=-5.5) == TIER_STANDARD_DIP


def test_classify_tier_shallow_dip_requires_8pct_drop(gatekeeper):
    # 0.50 ≤ %B < 0.70 with drop ≥ 8% → admitted as SHALLOW_DIP
    assert gatekeeper.classify_tier(pct_b=0.55, drop_pct=-8.0) == TIER_SHALLOW_DIP
    assert gatekeeper.classify_tier(pct_b=0.69, drop_pct=-12.5) == TIER_SHALLOW_DIP


def test_classify_tier_shallow_zone_with_small_drop_rejects(gatekeeper):
    # 0.50 ≤ %B < 0.70 but drop < 8% → REJECT (the AAOI/CRDO 2026-04-28 case)
    assert gatekeeper.classify_tier(pct_b=0.55, drop_pct=-7.99) == TIER_REJECT
    assert gatekeeper.classify_tier(pct_b=0.62, drop_pct=-5.5) == TIER_REJECT


def test_classify_tier_above_70_always_rejects(gatekeeper):
    # %B ≥ 0.70 → REJECT regardless of drop magnitude (the ARM 2026-04-28 case)
    assert gatekeeper.classify_tier(pct_b=0.70, drop_pct=-15.0) == TIER_REJECT
    assert gatekeeper.classify_tier(pct_b=0.98, drop_pct=-20.0) == TIER_REJECT


def test_classify_tier_handles_positive_drop_pct(gatekeeper):
    # Defensive: caller may pass +8.0 instead of -8.0; classifier uses abs()
    assert gatekeeper.classify_tier(pct_b=0.55, drop_pct=8.0) == TIER_SHALLOW_DIP


def test_check_technical_filters_returns_tier_deep_dip(gatekeeper):
    cached = {"close": 42.0, "bb_lower": 40.0, "bb_upper": 100.0, "volume": 2_000_000}
    # %B = (42-40)/(100-40) = 0.033 → DEEP_DIP
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="AAPL", cached_indicators=cached, drop_pct=-6.5
    )
    assert is_valid is True
    assert reasons["tier"] == TIER_DEEP_DIP


def test_check_technical_filters_returns_tier_shallow_when_drop_large(gatekeeper):
    cached = {"close": 50.0, "bb_lower": 40.0, "bb_upper": 60.0, "volume": 2_000_000}
    # %B = 0.5 — borderline shallow
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="MSFT", cached_indicators=cached, drop_pct=-9.0
    )
    assert is_valid is True
    assert reasons["tier"] == TIER_SHALLOW_DIP


def test_check_technical_filters_rejects_shallow_zone_with_small_drop(gatekeeper):
    cached = {"close": 50.0, "bb_lower": 40.0, "bb_upper": 60.0, "volume": 2_000_000}
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="MSFT", cached_indicators=cached, drop_pct=-5.0
    )
    assert is_valid is False
    assert reasons["tier"] == TIER_REJECT


def test_check_technical_filters_drop_pct_optional_defaults_to_zero(gatekeeper):
    """Backward-compat: callers that don't supply drop_pct still work; shallow zone rejects."""
    cached = {"close": 50.0, "bb_lower": 40.0, "bb_upper": 60.0, "volume": 2_000_000}
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="MSFT", cached_indicators=cached
    )
    assert is_valid is False
    assert reasons["tier"] == TIER_REJECT
