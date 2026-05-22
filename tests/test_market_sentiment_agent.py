import sys
import os
import unittest
from unittest.mock import MagicMock, patch
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from app.services.research_service import ResearchService
from app.models.market_state import MarketState

class TestMarketSentimentAgent(unittest.TestCase):

    def setUp(self):
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'fake_key'}):
            self.service = ResearchService()
            self.service.grounding_client = MagicMock()
            self.service.model = MagicMock()
            self.service.flash_model = MagicMock()

    def test_market_sentiment_prompt_contents(self):
        print("\nTesting Market Sentiment prompt builder...")
        ticker = "SIE.DE"
        state = MarketState(ticker=ticker, date="2025-12-18")
        raw_data = {"news_items": []}

        prompt = self.service._create_market_sentiment_prompt(state, raw_data)

        self.assertIn("SIE.DE", prompt)
        self.assertIn("TODAY and YESTERDAY", prompt)
        self.assertIn("listing market", prompt.lower())
        self.assertIn("business market", prompt.lower())
        self.assertIn("us market direction", prompt.lower())
        print("Test Passed: prompt contains required sections.")

    def test_market_sentiment_prompt_includes_market_news(self):
        print("\nTesting Market Sentiment prompt includes Market News...")
        state = MarketState(ticker="AAPL", date="2025-12-18")
        raw_data = {
            "news_items": [
                {
                    "provider": "Market News (Benzinga)",
                    "headline": "S&P 500 hits new high",
                    "datetime_str": "2025-12-18",
                    "summary": "Broad rally across sectors.",
                },
                {
                    "provider": "Company News",
                    "headline": "Unrelated",
                    "datetime_str": "2025-12-18",
                    "summary": "Should not appear.",
                },
            ]
        }

        prompt = self.service._create_market_sentiment_prompt(state, raw_data)
        self.assertIn("S&P 500 hits new high", prompt)
        self.assertIn("Broad rally across sectors.", prompt)
        self.assertNotIn("Unrelated", prompt)
        print("Test Passed: market-news-only items are included.")

class TestMarketSentimentVolatilityBlock(unittest.TestCase):

    def setUp(self):
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'fake_key'}):
            self.service = ResearchService()
            self.service.grounding_client = MagicMock()
            self.service.model = MagicMock()
            self.service.flash_model = MagicMock()

    def _regime(self):
        return {
            "vix": 16.75, "vix_class": "NORMAL", "vix_pctile_20d": 65.0,
            "term_structure": "CONTANGO", "term_spread": -1.45,
            "fear_greed": 42, "fear_greed_rating": "Fear",
            "regime_score": 0.48, "regime_label": "NEUTRAL",
            "summary": "VIX 16.75 (NORMAL), CONTANGO, trend BULL — regime NEUTRAL (0.48).",
        }

    def test_volatility_block_present_when_regime_set(self):
        state = MarketState(ticker="AAPL", date="2026-05-22", volatility_regime=self._regime())
        prompt = self.service._create_market_sentiment_prompt(state, {})
        self.assertIn("VOLATILITY REGIME", prompt)
        self.assertIn("16.75", prompt)
        self.assertIn("CONTANGO", prompt)
        self.assertIn("NEUTRAL", prompt)

    def test_no_volatility_block_when_regime_missing(self):
        state = MarketState(ticker="AAPL", date="2026-05-22")
        prompt = self.service._create_market_sentiment_prompt(state, {})
        self.assertNotIn("VOLATILITY REGIME", prompt)


if __name__ == "__main__":
    unittest.main()
