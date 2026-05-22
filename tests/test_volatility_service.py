from unittest.mock import patch

import pytest

from app.services.volatility_service import VolatilityService, classify_vix


def _history(values):
    """Build a FRED-style (value, date) list, newest first."""
    return [(str(v), f"2026-05-{21 - i:02d}") for i, v in enumerate(values)]


class TestClassifyVix:
    @pytest.mark.parametrize("level,expected", [
        (12.0, "COMPLACENT"),
        (17.0, "NORMAL"),
        (24.0, "ELEVATED"),
        (35.0, "PANIC"),
    ])
    def test_classify_vix_bands(self, level, expected):
        assert classify_vix(level) == expected


class TestGetVixContext:
    def test_returns_latest_level_class_and_percentiles(self):
        # newest value 18.0 sits above 15 of 20 trailing values
        values = [18.0] + [10.0] * 15 + [22.0] * 4
        svc = VolatilityService()
        with patch("app.services.volatility_service.fred_service.fetch_series_history",
                   return_value=_history(values)):
            ctx = svc.get_vix_context()
        assert ctx["vix"] == 18.0
        assert ctx["vix_date"] == "2026-05-21"
        assert ctx["vix_class"] == "NORMAL"
        assert ctx["vix_pctile_20d"] == 75.0  # 15 of 20 below 18.0
        assert "error" not in ctx

    def test_skips_fred_missing_marker(self):
        svc = VolatilityService()
        history = [(".", "2026-05-21"), ("16.5", "2026-05-20")]
        with patch("app.services.volatility_service.fred_service.fetch_series_history",
                   return_value=history):
            ctx = svc.get_vix_context()
        assert ctx["vix"] == 16.5
        assert ctx["vix_date"] == "2026-05-20"

    def test_fetch_failure_returns_error_dict(self):
        svc = VolatilityService()
        with patch("app.services.volatility_service.fred_service.fetch_series_history",
                   side_effect=RuntimeError("FRED down")):
            ctx = svc.get_vix_context()
        assert ctx["vix"] is None
        assert "FRED down" in ctx["error"]
