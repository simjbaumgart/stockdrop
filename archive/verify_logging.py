
import sys
import os
import json
from datetime import datetime

# Ensure app can be imported
sys.path.append(os.getcwd())

# Mock Environment
if not os.getenv("POLYGON_API_KEY"):
    os.environ["POLYGON_API_KEY"] = "MX8dLTzDgcUHHLh6GNE12iOzitcS_HCH"

from app.models.market_state import MarketState
from app.services.research_service import research_service

def test_prompt_generation_and_logging():
    print("Testing ResearchService Prompt + Logging...")
    
    # Create 35 mock news items from different sources
    mock_news = []
    for i in range(35):
        source = "Benzinga" if i % 3 == 0 else ("Yahoo" if i % 3 == 1 else "Finnhub")
        content = f"<p>Content for article {i}</p>" if source == "Benzinga" else ""
        
        mock_news.append({
            "source": source,
            "headline": f"News Item {i} from {source}",
            "datetime_str": "2024-12-10",
            "datetime": int(datetime.now().timestamp()) - i*3600, # older by hour
            "content": content
        })
    
    ticker = "LOG_TEST"
    date_str = datetime.now().strftime("%Y-%m-%d")
    state = MarketState(ticker=ticker, date=date_str)
    
    raw_data = {
        "news_items": mock_news,
        "transcript_text": "Mock transcript"
    }
    
    # Clear old log if exists
    log_file = f"data/news/{ticker}_{date_str}_news_context.txt"
    if os.path.exists(log_file):
        os.remove(log_file)
    
    # Execute
    print(f"Generating prompt for {ticker}...")
    prompt = research_service._create_news_agent_prompt(state, raw_data, "-5.00%")
    
    # Verify Logging
    if os.path.exists(log_file):
        print(f"\nSUCCESS: Log file created at {log_file}")
        with open(log_file, "r") as f:
            content = f.read()
            lines = content.splitlines()
            # Count items starting with "- "
            item_count = sum(1 for line in lines if line.startswith("- "))
            print(f"Items logged: {item_count}")
            
            if item_count == 30:
                print("SUCCESS: Log file contains exactly 30 items (Limit enforced).")
            else:
                print(f"WARNING: Log file contains {item_count} items (Expected 30).")
                
            if "News Item 0 from Benzinga" in content:
                print("SUCCESS: Benzinga item present.")
            if "Yahoo" in content and "Finnhub" in content:
                 print("SUCCESS: Mix of sources present.")
    else:
        print("\nFAILURE: Log file NOT created.")

if __name__ == "__main__":
    test_prompt_generation_and_logging()
