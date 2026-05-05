"""Regression: gatekeeper must report NaN explicitly, not 'Insufficient Drop'.

Production failure (PS, 2026-05-04): %B was NaN (insufficient price history
for the Bollinger window) and the gatekeeper correctly rejected, but the
reason-string fallthrough produced "13.6% < 8.0% (Insufficient Drop)" — a
misleading message because 13.6% > 8.0%.
"""
import math
from unittest.mock import patch
from app.services.gatekeeper_service import gatekeeper_service


def test_nan_pct_b_reports_nan_reason():
    fake_indicators = {
        "close": 100.0,
        "bb_lower": float("nan"),
        "bb_upper": float("nan"),
        "average_volume_10d": 5_000_000.0,
    }
    with patch(
        "app.services.gatekeeper_service.tradingview_service.get_technical_indicators",
        return_value=fake_indicators,
    ):
        is_valid, reasons = gatekeeper_service.check_technical_filters(
            symbol="PS", drop_pct=-13.6
        )

    assert is_valid is False
    bb_status = reasons.get("bb_status", "")
    assert "nan" in bb_status.lower(), f"expected NaN in bb_status, got: {bb_status!r}"
    assert "Insufficient Drop" not in bb_status, (
        f"NaN should not fall through to drop-size message: {bb_status!r}"
    )


def test_nan_only_in_lower_band_still_rejected():
    """If only one band is NaN, still reject with NaN reason."""
    fake_indicators = {
        "close": 100.0,
        "bb_lower": float("nan"),
        "bb_upper": 105.0,
        "average_volume_10d": 5_000_000.0,
    }
    with patch(
        "app.services.gatekeeper_service.tradingview_service.get_technical_indicators",
        return_value=fake_indicators,
    ):
        is_valid, reasons = gatekeeper_service.check_technical_filters(
            symbol="X", drop_pct=-10.0
        )

    assert is_valid is False
    assert "nan" in reasons.get("bb_status", "").lower()


def test_normal_path_unchanged_when_bands_are_valid():
    """Sanity: a real low-%B reading still classifies as a Deep Dip."""
    # price = 90, lower = 95, upper = 105 → %B = (90-95)/(105-95) = -0.5 (deep)
    fake_indicators = {
        "close": 90.0,
        "bb_lower": 95.0,
        "bb_upper": 105.0,
        "average_volume_10d": 5_000_000.0,
    }
    with patch(
        "app.services.gatekeeper_service.tradingview_service.get_technical_indicators",
        return_value=fake_indicators,
    ):
        is_valid, reasons = gatekeeper_service.check_technical_filters(
            symbol="X", drop_pct=-10.0
        )

    assert is_valid is True
    assert "Deep Dip" in reasons.get("bb_status", "")
    assert "nan" not in reasons.get("bb_status", "").lower()
