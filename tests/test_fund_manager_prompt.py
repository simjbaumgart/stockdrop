import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.research_service import ResearchService
from app.models.market_state import MarketState


def _regime():
    return {
        "vix": 24.0, "vix_class": "ELEVATED", "vix_pctile_20d": 90.0,
        "term_structure": "BACKWARDATION", "term_spread": 1.2,
        "fear_greed": 22, "fear_greed_rating": "Extreme Fear",
        "regime_score": 0.71, "regime_label": "FAVORABLE",
        "summary": "VIX 24.0 (ELEVATED), BACKWARDATION, trend BULL — "
                   "regime FAVORABLE (0.71) for dip-buying.",
    }


class TestFundManagerPrompt(unittest.TestCase):

    def setUp(self):
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'fake_key'}):
            self.svc = ResearchService()
            self.svc.grounding_client = MagicMock()
            self.svc.model = MagicMock()
            self.svc.flash_model = MagicMock()

    def test_volatility_block_present_when_regime_set(self):
        state = MarketState(ticker="AAPL", date="2026-05-22", volatility_regime=_regime())
        prompt = self.svc._create_fund_manager_prompt(state, [], [], "-6.0%")
        self.assertIn("VOLATILITY REGIME", prompt)
        self.assertIn("24.0", prompt)
        self.assertIn("BACKWARDATION", prompt)
        self.assertIn("FAVORABLE", prompt)

    def test_no_volatility_block_when_regime_missing(self):
        state = MarketState(ticker="AAPL", date="2026-05-22")
        prompt = self.svc._create_fund_manager_prompt(state, [], [], "-6.0%")
        self.assertNotIn("VOLATILITY REGIME", prompt)


if __name__ == "__main__":
    unittest.main()
