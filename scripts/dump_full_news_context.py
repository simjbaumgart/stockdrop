import sys
import os
import json

# Ensure app module is found
sys.path.append(os.getcwd())

from app.models.market_state import MarketState
from app.services.research_service import research_service
from app.services.stock_service import StockService

def dump_news_context():
    tickers = ["GOOG", "RIVN"]
    output_file = "news_context_dump.txt"
    stock_service = StockService()
    
    with open(output_file, "w") as f:
        f.write("=== FULL NEWS AGENT CONTEXT DUMP ===\n")
        f.write("This file contains the exact text string injected into the News Agent prompt.\n")
        f.write("==========================================================================\n\n")
    
    for ticker in tickers:
        print(f"Processing {ticker}...")
        
        # 1. Fetch Aggregated News (exactly as the real flow does)
        # We assume 'US' region for these examples
        print(f"  Fetching aggregated news for {ticker}...")
        news_items = stock_service.get_aggregated_news(ticker, region="US")
        
        # 2. Simulate raw_data
        raw_data = {
            "news_items": news_items,
            "transcript_text": "Mock transcript for context."
        }
        
        # 3. Generate Prompt
        state = MarketState(ticker=ticker, date="2025-12-12")
        prompt = research_service._create_news_agent_prompt(state, raw_data, "-5.00%")
        
        # Dump the entire prompt to let the user see the instructions/context too
        content_to_save = prompt
            
        with open(output_file, "a") as f:
            f.write(f"\n>> TICKER: {ticker}\n")
            f.write(f"Total Items Passed: {len(news_items)}\n")
            f.write("-" * 50 + "\n")
            f.write(content_to_save)
            f.write("\n" + "=" * 50 + "\n")
            
    print(f"\nDump complete. Saved to {os.path.abspath(output_file)}")

if __name__ == "__main__":
    dump_news_context()
