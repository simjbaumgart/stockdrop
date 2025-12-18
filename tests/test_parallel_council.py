
import unittest
from unittest.mock import MagicMock, patch
import json
import time
from app.services.research_service import ResearchService
from app.models.market_state import MarketState

class TestParallelCouncil(unittest.TestCase):
    def setUp(self):
        self.service = ResearchService()
        # Mock models to avoid API calls
        self.service.model = MagicMock()
        self.service.flash_model = MagicMock()
        self.service.grounding_client = MagicMock()
        
        # Mock Model Responses
        self.service.model.generate_content.return_value.text = "Mocked Response"
        self.service.flash_model.generate_content.return_value.text = "Mocked Flash Response"
        
        # Mock Grounding Client Response
        mock_candidate = MagicMock()
        mock_candidate.content.parts = [MagicMock(text="Mocked Grounded Response")]
        mock_candidate.grounding_metadata = None
        
        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]
        self.service.grounding_client.models.generate_content.return_value = mock_response

    def test_parallel_execution_time(self):
        """
        Verify that 4 agents running with a sleep delay execute in parallel (time < sum(delays)).
        """
        # Mock _call_agent to simulate delay
        original_call = self.service._call_agent
        
        def delayed_call(prompt, agent_name, state=None):
            time.sleep(1) # Simulate 1s work
            if state:
                with self.service.lock:
                    state.agent_calls += 1
            return f"{agent_name} Report"
            
        # We also need to mock _call_market_sentiment_agent and _call_competitive_agent if they are separate
        # In current impl, _call_agent calls them or they are called directly in analyze_stock loop.
        
        # Let's inspect analyze_stock loop in research_service.py:
        # It calls:
        # 1. _call_agent(tech)
        # 2. _call_agent(news)
        # 3. _call_market_sentiment_agent(ticker, state)
        # 4. _call_agent(comp)
        
        # We need to mock these methods on the instance
        self.service._call_agent = MagicMock(side_effect=delayed_call)
        
        def delayed_sentiment(ticker, state):
            time.sleep(1)
            with self.service.lock:
                state.agent_calls += 1
            return "Sentiment Report"
            
        self.service._call_market_sentiment_agent = MagicMock(side_effect=delayed_sentiment)
        
        # Input Data
        raw_data = {"change_percent": -5.0, "news_items": [], "indicators": {}}
        
        print("Starting Parallel Test (Expected 4s work in ~1s)...")
        start_time = time.time()
        
        # Run Analyze Stock (Partial)
        # Result dict is complex, we just check side effects
        try:
             # We assume analyze_stock will call these.
             # Note: analyze_stock also calls _run_debate etc. which we want to skip or mock to be fast.
             # Let's mock _run_debate and _run_risk_council to do nothing.
             self.service._run_debate = MagicMock()
             self.service._run_risk_council_and_decision = MagicMock(return_value={"action": "HOLD", "score": 50, "reason": "Test"})
             self.service._run_deep_reasoning_check = MagicMock(return_value="Deep Report")
             
             result = self.service.analyze_stock("TEST", raw_data)
             
        except Exception as e:
            self.fail(f"Analyze Stock failed: {e}")
            
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"Total Duration: {duration:.2f}s")
        
        # Assertions
        # 4 tasks * 1s each. Parallel should be ~1s. Sequential would be 4s.
        # Allow some overhead, so < 2.5s is a pass for parallel.
        self.assertLess(duration, 2.5, "Execution took too long, ensuring parallelism.")
        
        # Verify Agent Calls Count
        # 4 agents + maybe others? 
        # _run_debate is mocked, so 0 calls there.
        # _run_risk is mocked.
        # So we expect exactly 4 calls from the first phase.
        
        # We need to check 'agent_calls' in the returned dict
        # The 'result' dict contains "agent_calls".
        # But wait, we mocked the methods that increment it?
        # Yes, delayed_call increments it.
        
        total_calls = result.get("agent_calls", 0)
        print(f"Total Agent Calls: {total_calls}")
        self.assertEqual(total_calls, 4)
        
        # Verify Reports are present
        detailed = result.get("detailed_report", "")
        self.assertIn("Technical Agent", str(self.service._call_agent.call_args_list))
        # Note: Detailed report content check is harder since we mocked the output string effectively.
        
if __name__ == '__main__':
    unittest.main()
