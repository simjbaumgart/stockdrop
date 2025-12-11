
import sys
import os
import logging
sys.path.append(os.getcwd())

from app.services.research_service import research_service
from app.models.market_state import MarketState

# Ensure we have data
SYMBOL = "GOOG"
DATA_DIR = f"data/DefeatBeta_data/{SYMBOL}"

if not os.path.exists(DATA_DIR):
    print(f"Error: Data directory {DATA_DIR} does not exist. Run compile_data_goog.py first.")
    sys.exit(1)

def verify_integration():
    print(f"Verifying News Agent Integration for {SYMBOL}...")
    
    # Mock State and Data
    state = MarketState(ticker=SYMBOL, date="2025-12-07")
    raw_data = {
        "news_items": [{"headline": "Old Mock News", "datetime_str": "2025-01-01"}],
        "transcript_text": "Old Mock Transcript"
    }
    
    # Call the private method to generate prompt
    # Note: accessing private method for verification
    try:
        prompt = research_service._create_news_agent_prompt(state, raw_data, "-5.0%")
        
        print("\n--- GENERATED PROMPT SNIPPET ---")
        print(prompt[:2000]) # Print first 2000 chars
        
        print("\n--- CHECKS ---")
        
        if "ADDITIONAL NEWS SOURCES (DefeatBeta)" in prompt:
            print("[PASS] DefeatBeta News section found.")
        else:
            print("[FAIL] DefeatBeta News section NOT found.")
            
        if "ADDITIONAL TRANSCRIPT DATA (DefeatBeta)" in prompt or "Earnings Call Transcript - Date:" in prompt:
             # Depending on if it replaced or appended
            print("[PASS] DefeatBeta Transcript data found.")
        else:
             print("[FAIL] DefeatBeta Transcript data NOT found.")
             
        if "Treat this as **additional information**" in prompt:
            print("[PASS] User instruction found.")
        else:
            print("[FAIL] User instruction NOT found.")

        if "Extended Transcript Summary" in prompt:
            print("[PASS] Extended Transcript Summary instruction found.")
        else:
            print("[FAIL] Extended Transcript Summary instruction NOT found.")

    except Exception as e:
        print(f"Error calling create_news_agent_prompt: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_integration()
