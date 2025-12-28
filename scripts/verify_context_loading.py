
import sys
import os
import time
import logging

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.deep_research_service import deep_research_service

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def verify_context_loading():
    print("--- Verifying Context Loading ---")
    
    # Mock Candidates (using real files found: 9024_2025-12-22)
    # 9024 exists. Let's try it.
    candidates = [
        {'symbol': '9024', 'timestamp': '2025-12-22 10:00:00'},
        {'symbol': 'FAKE_TICKER', 'timestamp': '2025-12-22 10:00:00'}
    ]
    
    print(f"Testing with candidates: {[c['symbol'] for c in candidates]}")
    
    # We want to check if _load_council_report works
    report = deep_research_service._load_council_report('9024', '2025-12-22')
    if report:
        print("SUCCESS: Loaded report for 9024")
        print(f"Length: {len(report)} chars")
    else:
        print("FAILURE: Could not load report for 9024")
        
    report_fake = deep_research_service._load_council_report('FAKE_TICKER', '2025-12-22')
    if not report_fake:
        print("SUCCESS: Correctly returned empty for FAKE_TICKER")
    else:
        print("FAILURE: Found report for FAKE_TICKER??")

    # Manually inspect the prompt construction logic (by calling internal methods or just trusting the previous step)
    # We can't easily intercept the prompt print without subclassing or mocking.
    # But since execute_batch_comparison prints "DEBUG: SUBMISSION PROMPT", we can just run it
    # BUT we don't want to actually call the API.
    # So we will MOCK the requests.post to avoid API cost/error.
    
    import requests
    original_post = requests.post
    
    def mock_post(*args, **kwargs):
        print("MOCK: Caught API Call")
        # Return a fake response
        class MockResponse:
            status_code = 200
            def json(self): return {'id': 'mock_interaction_id'}
            @property
            def text(self): return ""
        return MockResponse()

    requests.post = mock_post
    
    # Also mock get for polling
    def mock_get(*args, **kwargs):
        class MockResponse:
            status_code = 200
            def json(self): 
                return {
                    'status': 'completed', 
                    'outputs': [{'text': '{"winner_symbol": "9024", "ranking": ["9024"]}'}]
                }
        return MockResponse()
    requests.get = mock_get

    print("\n--- Running execute_batch_comparison (MOCKED) ---")
    deep_research_service.execute_batch_comparison(candidates, batch_id=999)

if __name__ == "__main__":
    verify_context_loading()
