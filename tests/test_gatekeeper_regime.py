from unittest.mock import patch

from app.services.gatekeeper_service import GatekeeperService


_FAKE_REGIME = {
    "trend": "BULL",
    "vix": 16.75,
    "vix_class": "NORMAL",
    "term_structure": "CONTANGO",
    "term_spread": -1.45,
    "fear_greed": 42,
    "fear_greed_rating": "Fear",
    "regime_score": 0.48,
    "regime_label": "NEUTRAL",
    "errors": [],
    "summary": "VIX 16.75 (NORMAL), CONTANGO, trend BULL — regime NEUTRAL (0.48).",
}


class TestCheckMarketRegime:
    def test_merges_volatility_and_keeps_legacy_keys(self):
        gk = GatekeeperService()
        with patch("app.services.gatekeeper_service.tradingview_service"
                   ".get_technical_indicators",
                   return_value={"close": 500.0, "sma200": 480.0}), \
             patch("app.services.gatekeeper_service.volatility_service.get_regime",
                   return_value=dict(_FAKE_REGIME)) as mreg:
            result = gk.check_market_regime()
        assert result["regime"] == "BULL"
        assert "above" in result["details"]
        assert result["vix"] == 16.75
        assert result["regime_score"] == 0.48
        assert result["regime_label"] == "NEUTRAL"
        mreg.assert_called_once_with(trend="BULL")

    def test_bear_trend_passed_to_volatility(self):
        gk = GatekeeperService()
        with patch("app.services.gatekeeper_service.tradingview_service"
                   ".get_technical_indicators",
                   return_value={"close": 460.0, "sma200": 480.0}), \
             patch("app.services.gatekeeper_service.volatility_service.get_regime",
                   return_value=dict(_FAKE_REGIME)) as mreg:
            gk.check_market_regime()
        mreg.assert_called_once_with(trend="BEAR")

    def test_unknown_trend_still_attaches_volatility(self):
        gk = GatekeeperService()
        with patch("app.services.gatekeeper_service.tradingview_service"
                   ".get_technical_indicators", return_value=None), \
             patch("app.services.gatekeeper_service.volatility_service.get_regime",
                   return_value=dict(_FAKE_REGIME)) as mreg:
            result = gk.check_market_regime()
        assert result["regime"] == "UNKNOWN"
        assert result["vix"] == 16.75
        mreg.assert_called_once_with(trend="UNKNOWN")
