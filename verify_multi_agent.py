import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add app to path
sys.path.append(os.getcwd())

from app.services.research_service import ResearchService

class TestMultiAgentResearch(unittest.TestCase):
    def setUp(self):
        self.service = ResearchService()
        # Mock the model to avoid real API calls
        self.service.model = MagicMock()

    def test_analyze_stock_orchestration(self):
        print("Testing Multi-Agent Orchestration...")
        
        # Mock responses for each agent
        def side_effect(prompt):
            if "Senior Financial Analyst" in prompt:
                return MagicMock(text="ANALYST REPORT: The stock dropped due to earnings miss.")
            elif "Aggressive Growth Fund Manager" in prompt:
                return MagicMock(text="BULL CASE: This is a massive buying opportunity.")
            elif "Risk Manager" in prompt:
                return MagicMock(text="BEAR CASE: The company is doomed.")
            elif "Chief Investment Officer" in prompt:
                return MagicMock(text="RECOMMENDATION: BUY\nEXECUTIVE SUMMARY: Buy the dip.\nDETAILED REPORT: Detailed analysis here.")
            return MagicMock(text="Unknown prompt")

        self.service.model.generate_content.side_effect = side_effect

        # Run analysis
        result = self.service.analyze_stock("AAPL", "Apple Inc.", 150.0, -5.5)

        # Verify results
        self.assertEqual(result["recommendation"], "BUY")
        self.assertEqual(result["executive_summary"], "Buy the dip.")
        self.assertIn("Detailed analysis here", result["detailed_report"])
        
        # Verify call count (1 Analyst + 1 Bull + 1 Bear + 1 Synthesizer = 4 calls)
        self.assertEqual(self.service.model.generate_content.call_count, 4)
        print("SUCCESS: Orchestration verified with 4 agent calls.")

if __name__ == "__main__":
    unittest.main()
