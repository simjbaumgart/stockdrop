from unittest.mock import MagicMock, patch

import pandas as pd
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


def _yf_close_frame(vix_series, vix3m_series, dates):
    """Build a yfinance-style multi-ticker download frame."""
    cols = pd.MultiIndex.from_tuples([("Close", "^VIX"), ("Close", "^VIX3M")])
    return pd.DataFrame(
        {("Close", "^VIX"): vix_series, ("Close", "^VIX3M"): vix3m_series},
        index=pd.to_datetime(dates),
        columns=cols,
    )


class TestGetTermStructure:
    def test_contango_when_vix_below_vix3m(self):
        svc = VolatilityService()
        frame = _yf_close_frame([16.0, 16.75], [18.0, 18.20],
                                ["2026-05-20", "2026-05-21"])
        with patch("app.services.volatility_service.yf.download", return_value=frame):
            ts = svc.get_term_structure()
        assert ts["vix_spot"] == 16.75
        assert ts["vix3m"] == 18.20
        assert ts["term_spread"] == -1.45
        assert ts["term_structure"] == "CONTANGO"

    def test_backwardation_when_vix_above_vix3m(self):
        svc = VolatilityService()
        frame = _yf_close_frame([30.0, 32.0], [28.0, 29.0],
                                ["2026-05-20", "2026-05-21"])
        with patch("app.services.volatility_service.yf.download", return_value=frame):
            ts = svc.get_term_structure()
        assert ts["term_spread"] == 3.0
        assert ts["term_structure"] == "BACKWARDATION"

    def test_fetch_failure_returns_error_dict(self):
        svc = VolatilityService()
        with patch("app.services.volatility_service.yf.download",
                   side_effect=RuntimeError("yahoo down")):
            ts = svc.get_term_structure()
        assert ts["term_spread"] is None
        assert "yahoo down" in ts["error"]


class TestGetFearGreed:
    def test_parses_score_and_rating(self):
        svc = VolatilityService()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "fear_and_greed": {"score": 41.6, "rating": "Fear"}
        })
        with patch("app.services.volatility_service.requests.get", return_value=resp):
            fg = svc.get_fear_greed()
        assert fg["fear_greed"] == 42  # rounded
        assert fg["fear_greed_rating"] == "Fear"

    def test_failure_is_non_fatal_returns_none(self):
        svc = VolatilityService()
        with patch("app.services.volatility_service.requests.get",
                   side_effect=RuntimeError("cnn 418")):
            fg = svc.get_fear_greed()
        assert fg["fear_greed"] is None
        assert fg["fear_greed_rating"] is None


class TestScoreRegime:
    def test_bull_elevated_backwardation_scores_favorable(self):
        # trend 0.65, vix ELEVATED 0.85, term spread +2 -> 1.0
        score = VolatilityService.score_regime("BULL", "ELEVATED", 2.0)
        assert score == round(0.40 * 0.65 + 0.35 * 0.85 + 0.25 * 1.0, 3)
        assert score >= 0.60

    def test_bear_complacent_contango_scores_unfavorable(self):
        # trend 0.35, vix COMPLACENT 0.30, term spread -3 -> clamped 0.0
        score = VolatilityService.score_regime("BEAR", "COMPLACENT", -3.0)
        assert score == round(0.40 * 0.35 + 0.35 * 0.30 + 0.25 * 0.0, 3)
        assert score < 0.40

    def test_unknown_trend_and_missing_spread_use_neutral_defaults(self):
        score = VolatilityService.score_regime("UNKNOWN", "NORMAL", None)
        assert score == round(0.40 * 0.50 + 0.35 * 0.50 + 0.25 * 0.50, 3)


class TestGetRegime:
    def _patch_all(self, vix_ctx, term_ctx, fg_ctx):
        return [
            patch.object(VolatilityService, "get_vix_context", return_value=vix_ctx),
            patch.object(VolatilityService, "get_term_structure", return_value=term_ctx),
            patch.object(VolatilityService, "get_fear_greed", return_value=fg_ctx),
        ]

    def test_assembles_full_regime_dict(self):
        svc = VolatilityService()
        patches = self._patch_all(
            {"vix": 16.75, "vix_date": "2026-05-21", "vix_class": "NORMAL",
             "vix_pctile_5d": 80.0, "vix_pctile_20d": 65.0},
            {"vix3m": 18.20, "term_spread": -1.45, "term_structure": "CONTANGO"},
            {"fear_greed": 42, "fear_greed_rating": "Fear"},
        )
        for p in patches:
            p.start()
        try:
            regime = svc.get_regime(trend="BULL")
        finally:
            for p in patches:
                p.stop()
        assert regime["vix"] == 16.75
        assert regime["term_structure"] == "CONTANGO"
        assert regime["fear_greed"] == 42
        assert regime["trend"] == "BULL"
        assert 0.0 <= regime["regime_score"] <= 1.0
        assert regime["regime_label"] in ("FAVORABLE", "NEUTRAL", "UNFAVORABLE")
        assert "VIX 16.75" in regime["summary"]
        assert regime["errors"] == []

    def test_collects_component_errors(self):
        svc = VolatilityService()
        patches = self._patch_all(
            {"vix": None, "error": "FRED down"},
            {"term_spread": None, "error": "yahoo down"},
            {"fear_greed": None, "fear_greed_rating": None},
        )
        for p in patches:
            p.start()
        try:
            regime = svc.get_regime(trend="UNKNOWN")
        finally:
            for p in patches:
                p.stop()
        assert any("FRED down" in e for e in regime["errors"])
        assert any("yahoo down" in e for e in regime["errors"])
        # vix_class falls back to NORMAL so scoring still produces a number
        assert regime["regime_score"] is not None

    def test_caches_within_ttl_for_same_trend(self):
        svc = VolatilityService()
        with patch.object(VolatilityService, "get_vix_context",
                          return_value={"vix": 16.0, "vix_class": "NORMAL"}) as m, \
             patch.object(VolatilityService, "get_term_structure",
                          return_value={"term_spread": 0.0, "term_structure": "CONTANGO"}), \
             patch.object(VolatilityService, "get_fear_greed",
                          return_value={"fear_greed": None, "fear_greed_rating": None}):
            svc.get_regime(trend="BULL")
            svc.get_regime(trend="BULL")
            assert m.call_count == 1  # second call served from cache
