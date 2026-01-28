import sys
import os
import json

# Ensure app module is found
sys.path.append(os.getcwd())

from app.services.stock_service import StockService

def inspect_other_sources():
    ticker = "GOOG" # Good candidate for volume
    stock_service = StockService()
    
    print(f"Fetching aggregated news for {ticker}...")
    news_items = stock_service.get_aggregated_news(ticker, region="US")
    
    # Filter out Massive/Benzinga to see what others look like
    others = [n for n in news_items if "Massive" not in n.get('source', '')]
    
    print(f"\nFound {len(others)} non-Massive items.")
    
    # Group by source to see one example of each
    by_source = {}
    for item in others:
        s = item.get('source', 'Unknown')
        if s not in by_source:
            by_source[s] = item
            
    for source, item in by_source.items():
        print(f"\n--- SOURCE: {source} ---")
        print(f"Keys: {list(item.keys())}")
        print(f"Headline: {item.get('headline', 'N/A')}")
        print(f"Summary: {item.get('summary', 'N/A')}")
        print(f"Content: {item.get('content', 'N/A')}")

if __name__ == "__main__":
    inspect_other_sources()
