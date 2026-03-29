
import sys
import os
import json
from datetime import datetime

# Ensure app can be imported
sys.path.append(os.getcwd())

# Load Environment
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())
except ImportError:
    pass

if not os.getenv("POLYGON_API_KEY"):
    print("Warning: POLYGON_API_KEY not set. Set it in your .env file.")

from app.models.market_state import MarketState
from app.services.research_service import research_service

def test_prompt_generation():
    print("Testing ResearchService Prompt Generation with Benzinga-like data...")
    
    # create mock Benzinga news items
    mock_news = [
        {
            "source": "Benzinga",
            "headline": "CEO Announces Big AI Breakthrough",
            "datetime_str": "2024-12-10",
            "datetime": int(datetime.now().timestamp()), # Now
            "content": "<p>Full HTML content of the article goes here. It is very important.</p>"
        },
        {
            "source": "Benzinga",
            "headline": "Stock Drops due to Panic",
            "datetime_str": "2024-12-09",
            "datetime": int(datetime.now().timestamp()) - 86400, # Yesterday
            "content": "Another full article body text."
        }
    ]
    
    # Mock Market State
    state = MarketState(ticker="TEST", date="2024-12-10")
    
    # Raw Data
    raw_data = {
        "news_items": mock_news,
        "transcript_text": "Mock transcript"
    }
    
    # Generate Prompt
    prompt = research_service._create_news_agent_prompt(state, raw_data, "-5.00%")
    
    print("\n--- GENERATED PROMPT SNIPPET ---")
    print(prompt[:2000]) # Print first 2000 chars to check formatting
    print("--------------------------------")
    
    # Verification
    if "CONTENT: <p>Full HTML" in prompt:
         print("\nSUCCESS: Full content found in prompt.")
    else:
         print("\nFAILURE: Full content NOT found.")

if __name__ == "__main__":
    test_prompt_generation()
