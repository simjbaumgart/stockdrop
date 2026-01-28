
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.stock_service import stock_service
from app.services.research_service import research_service
from app.models.market_state import MarketState

def verify_integration():
    print("--- Verifying US Stock Integration (AAPL) ---")
    
    # 1. Test StockService Aggregation
    print("\n1. Calling stock_service.get_aggregated_news('AAPL', region='US')...")
    news_items = stock_service.get_aggregated_news("AAPL", region="US")
    
    market_news_count = 0
    for item in news_items:
        if item.get('provider') == 'Market News (Benzinga)':
            market_news_count += 1
            
    print(f"   Total Items: {len(news_items)}")
    print(f"   Market News Items Found: {market_news_count}")
    
    if market_news_count > 0:
        print("   -> StockService Integration: PASS")
    else:
        print("   -> StockService Integration: FAIL (No Market News found)")

    # 2. Test ResearchService Prompt Generation
    print("\n2. Testing ResearchService Prompt Generation (Mock)...")
    
    # Create mock state
    state = MarketState(ticker="AAPL", date="2024-12-28")
    
    # Create mock raw_data with our news items
    raw_data = {
        "news_items": news_items,
        "transcript_text": "Mock Transcript",
        "change_percent": -5.0
    }
    
    prompt = research_service._create_news_agent_prompt(state, raw_data, "-5.0%")
    
    # Check if prompt contains the header
    if "--- BROAD MARKET CONTEXT (SPY/DIA/QQQ) ---" in prompt:
        print("   -> ResearchService Prompt: PASS (Header found)")
        # Print snippet
        start = prompt.find("--- BROAD MARKET CONTEXT (SPY/DIA/QQQ) ---")
        end = prompt.find("--- SOURCE: Benzinga/Massive ---")
        if end == -1: end = start + 500
        print(f"   Snippet:\n{prompt[start:end]}...")
    else:
        print("   -> ResearchService Prompt: FAIL (Header not found)")
        print("   Prompt Snippet (Start of News Section):")
        start = prompt.find("INPUT DATA:")
        print(prompt[start:start+500])

if __name__ == "__main__":
    verify_integration()
