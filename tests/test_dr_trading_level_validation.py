"""Regression: DR results with garbage trading levels must not overwrite DB.

Production bug: IDCC got entry=0.0-0.0, stop=0.0 written to DB because
_repair_json_using_flash returned a structurally-valid object with all
zeros, and there was no plausibility gate.
"""
from app.services.deep_research_service import DeepResearchService


def _svc():
    return DeepResearchService.__new__(DeepResearchService)


def test_validates_all_zero_levels_as_invalid():
    result = {
        "entry_price_low": 0.0,
        "entry_price_high": 0.0,
        "stop_loss": 0.0,
        "review_verdict": "CONFIRMED",
    }
    ok, reason = _svc()._validate_trading_levels(result)
    assert ok is False
    assert "non-positive" in reason.lower() or "zero" in reason.lower()


def test_validates_negative_levels_as_invalid():
    result = {
        "entry_price_low": -10.0,
        "entry_price_high": -5.0,
        "stop_loss": -15.0,
    }
    ok, _ = _svc()._validate_trading_levels(result)
    assert ok is False


def test_validates_stop_above_entry_as_invalid():
    result = {
        "entry_price_low": 50.0,
        "entry_price_high": 55.0,
        "stop_loss": 60.0,  # stop above entry — wrong direction
    }
    ok, reason = _svc()._validate_trading_levels(result)
    assert ok is False
    assert "stop" in reason.lower()


def test_validates_entry_high_below_low_as_invalid():
    result = {
        "entry_price_low": 55.0,
        "entry_price_high": 50.0,  # high < low
        "stop_loss": 45.0,
    }
    ok, _ = _svc()._validate_trading_levels(result)
    assert ok is False


def test_validates_plausible_levels_as_ok():
    result = {
        "entry_price_low": 50.0,
        "entry_price_high": 55.0,
        "stop_loss": 45.0,
    }
    ok, _ = _svc()._validate_trading_levels(result)
    assert ok is True


def test_missing_levels_treated_as_invalid():
    result = {"review_verdict": "OVERRIDDEN"}
    ok, _ = _svc()._validate_trading_levels(result)
    assert ok is False


def test_non_numeric_levels_treated_as_invalid():
    result = {"entry_price_low": "abc", "entry_price_high": 55.0, "stop_loss": 45.0}
    ok, reason = _svc()._validate_trading_levels(result)
    assert ok is False
    assert "non-numeric" in reason.lower() or "abc" in str(reason)
