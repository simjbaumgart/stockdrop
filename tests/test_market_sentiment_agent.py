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

if __name__ == "__main__":
    unittest.main()
