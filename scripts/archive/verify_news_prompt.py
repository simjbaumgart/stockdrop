import sys
import os
import json

# Ensure app module is found
sys.path.append(os.getcwd())

from app.models.market_state import MarketState
from app.services.research_service import research_service
from app.services.benzinga_service import benzinga_service

def verify_news_prompt():
    tickers = ["GOOG", "RIVN"]
    
    print("=== VERIFYING NEWS AGENT PROMPT INPUTS ===\n")
    
    for ticker in tickers:
        print(f"\n>> PROCESSING TICKER: {ticker}")
        
        # 1. Fetch real news using BenzingaService
        print(f"Fetching news for {ticker}...")
        news_items = benzinga_service.get_company_news(ticker)
        
        # 2. Simulate raw_data structure expected by ResearchService
        raw_data = {
            "news_items": news_items,
            "transcript_text": "Mock transcript for verification."
        }
        
        # 3. Create dummy MarketState
        state = MarketState(ticker=ticker, date="2025-12-12")
        
        # 4. Generate the Prompt using the internal method
        # We access the protected method _create_news_agent_prompt for verification
        try:
            prompt = research_service._create_news_agent_prompt(state, raw_data, "-5.00%")
            
            print(f"\n[GENERATED PROMPT SEGMENT FOR {ticker}]")
            print("="*60)
            
            # Extract just the News Headlines section for clarity
            start_marker = "1. RECENT NEWS HEADLINES:"
            end_marker = "2. QUARTERLY REPORT SNIPPET"
            
            if start_marker in prompt and end_marker in prompt:
                start_idx = prompt.find(start_marker)
                end_idx = prompt.find(end_marker)
                print(prompt[start_idx:end_idx])
            else:
                print("Could not isolate news section. Printing first 2000 chars of prompt:")
                print(prompt[:2000])
                
            print("="*60)
            
        except Exception as e:
            print(f"Error generating prompt for {ticker}: {e}")

if __name__ == "__main__":
    verify_news_prompt()
