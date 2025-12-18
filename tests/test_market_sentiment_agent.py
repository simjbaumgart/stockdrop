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

    def test_market_sentiment_agent_call(self):
        print("\nTesting Market Sentiment Agent Call...")
        
        # Setup
        ticker = "SIE.DE"
        state = MarketState(ticker=ticker, date="2025-12-18")
        
        # Mock Response
        mock_response = MagicMock()
        mock_candidate = MagicMock()
        mock_part = MagicMock()
        mock_part.text = "Market is bullish. DAX up 1%. US Markets mixed."
        mock_candidate.content.parts = [mock_part]
        mock_candidate.grounding_metadata = None
        mock_response.candidates = [mock_candidate]
        
        self.service.grounding_client.models.generate_content.return_value = mock_response

        # Execute
        report = self.service._call_market_sentiment_agent(ticker, state)
        print(f"Report Generated:\n{report}")
        
        # Verify
        self.assertIn("Market is bullish", report)
        self.assertIn("(Generated with gemini-2.0-flash-exp & Google Search)", report)
        
        # Check Call Arguments
        self.service.grounding_client.models.generate_content.assert_called_once()
        call_args = self.service.grounding_client.models.generate_content.call_args
        
        # In unittest.mock call_args is a tuple (args, kwargs) or can be accessed via kwargs attr
        kwargs = call_args.kwargs
        self.assertEqual(kwargs['model'], 'gemini-2.0-flash-exp')
        
        prompt_sent = kwargs['contents']
        self.assertIn("SIE.DE", prompt_sent)
        self.assertIn("TODAY and YESTERDAY", prompt_sent)
        self.assertIn("listing market", prompt_sent.lower())
        self.assertIn("business market", prompt_sent.lower())
        self.assertIn("us market direction", prompt_sent.lower())
        print("Test Passed: Call arguments and response verification successful.")

    def test_market_sentiment_agent_no_client(self):
        print("\nTesting Market Sentiment Agent No Client...")
        self.service.grounding_client = None
        report = self.service._call_market_sentiment_agent("AAPL", MarketState("AAPL", "2025-10-10"))
        self.assertIn("Unavailable", report)
        print("Test Passed: Correctly handled missing client.")

if __name__ == "__main__":
    unittest.main()
