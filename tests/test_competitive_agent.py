import sys
import os
import json
import logging
from unittest.mock import MagicMock

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.research_service import ResearchService
from app.models.market_state import MarketState

# Configure logging
logging.basicConfig(level=logging.INFO)

def test_competitive_agent_direct():
    print("\n--- Testing Competitive Agent Direct Call ---")
    service = ResearchService()
    
    if not service.grounding_client:
        print("SKIPPING: No Grounding Client initialized (API Key missing?).")
        return

    prompt = "Who are the competitors of NVIDIA? List 3."
    print(f"Prompt: {prompt}")
    
    try:
        response = service._call_competitive_agent(prompt)
        print("\n[Response]:")
        print(response)
        
        if "Context: Competitive Landscape" in response:
            print("\nSUCCESS: Service returned response with correct context signature.")
        else:
            print("\nWARNING: Response missing context signature.")
            
    except Exception as e:
        print(f"\nERROR: {e}")

def test_prompt_generation():
    print("\n--- Testing Prompt Generation ---")
    service = ResearchService()
    state = MarketState(ticker="NVDA", date="2024-01-01")
    drop_str = "-5.0%"
    
    prompt = service._create_competitive_agent_prompt(state, drop_str)
    
    print("Generated Prompt Snippet:")
    print(prompt[:500] + "...")
    
    if "Competitive Landscape Agent" in prompt and "Summary & Key Points" in prompt:
        print("\nSUCCESS: Prompt contains required sections.")
    else:
        print("\nFAILURE: Prompt missing required sections.")

if __name__ == "__main__":
    test_prompt_generation()
    # Uncomment to run real API call (costs quota)
    # test_competitive_agent_direct()
    
    # We can run the real call if user permits, but let's try to run it once to be sure.
    test_competitive_agent_direct()
