import sys
import os
import logging

# Add app to path
sys.path.append(os.getcwd())

from app.services.research_service import research_service
from app.models.market_state import MarketState

# Mock methods to force a STRONG BUY decision without using real tokens/API calls for the other agents
def mock_run_risk_council_and_decision(state, drop_str):
    print("  [MOCK] Forcing STRONG BUY decision for verification...")
    return {
        "action": "STRONG BUY",
        "size": "Standard",
        "score": 95,
        "reason": "Mocked Strong Buy for testing deep reasoning integration.",
        "key_decision_points": ["Mock Point 1", "Mock Point 2"]
    }

def mock_call_agent(prompt, agent_name, state=None):
    if agent_name == "Technical Agent":
        return "Technicals look great. RSI is oversold. Bullish divergence."
    if agent_name == "News Agent":
        return "News implies overreaction. CEO bought shares."
    if "Bull" in agent_name:
        return "This is the buying opportunity of the decade."
    if "Bear" in agent_name:
         return "It is risky but maybe worth a shot."
    if "Fund Manager" in agent_name:
        return '{"action": "STRONG BUY", "score": 95, "reason": "Go for it."}'
    return "Mock Agent Output"

# Monkey patch
research_service._run_risk_council_and_decision = mock_run_risk_council_and_decision
research_service._call_agent = mock_call_agent
# Make sure we don't mock the internal methods called by _run_deep_reasoning_check if it calls _call_agent or requests directly.
# The `_run_deep_reasoning_check` uses `requests` directly, so monkey patching `_call_agent` won't break it.

def run_verification():
    ticker = "TEST"
    raw_data = {
        "change_percent": -10.0,
        "news_items": [],
        "transcript_text": "We are doing great."
    }
    
    print(f"Running verification for {ticker}...")
    result = research_service.analyze_stock(ticker, raw_data)
    
    print("\n\n--- FINAL OUTPUT CHECK ---")
    
    deep_report = result.get('deep_reasoning_report', '')
    if deep_report:
        print("[SUCCESS] Deep Reasoning Report Found:")
        print(deep_report[:200] + "...")
    else:
        print("[FAILURE] Deep Reasoning Report NOT found in result.")

    if "DEEP REASONING VERDICT" in result.get('detailed_report', ''):
        print("[SUCCESS] Deep Reasoning section found in Detailed Report.")
    else:
        print("[FAILURE] Deep Reasoning section MISSING from Detailed Report.")

if __name__ == "__main__":
    run_verification()
