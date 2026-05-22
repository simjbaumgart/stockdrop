"""Tests for _build_deep_research_context including volatility_regime field."""
from unittest.mock import patch

_FAKE_REGIME = {
    "regime": "BULL",
    "vix": 16.75,
    "vix_class": "NORMAL",
    "term_structure": "CONTANGO",
    "regime_score": 0.48,
    "regime_label": "NEUTRAL",
    "summary": "VIX is normal; term structure is in contango; regime score is neutral.",
}


def _make_service():
    """Create a bare StockService instance without running __init__."""
    from app.services.stock_service import StockService
    return StockService.__new__(StockService)


class TestBuildDeepResearchContext:
    def test_context_includes_volatility_regime(self):
        svc = _make_service()
        with patch(
            "app.services.stock_service.gatekeeper_service.check_market_regime",
            return_value=_FAKE_REGIME,
        ):
            ctx = svc._build_deep_research_context(
                report_data={"recommendation": "BUY"},
                raw_data={"change_percent": -6.0},
            )
        assert ctx["volatility_regime"] == _FAKE_REGIME
        assert ctx["volatility_regime"]["regime_score"] == 0.48
