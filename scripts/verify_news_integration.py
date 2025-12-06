
import sys
import os

# Add project root to sys.path
sys.path.append(os.getcwd())

from app.services.stock_service import stock_service
import json

def verify_news_aggregation(symbol):
    print(f"\nVerifying News Aggregation for {symbol}...")
    try:
        news = stock_service.get_aggregated_news(symbol)
        print(f"Aggregated {len(news)} news items.")
        
        if not news:
            print("WARNING: No news returned.")
            return

        # Check structure
        first_item = news[0]
        required_keys = ["source", "headline", "summary", "url", "datetime", "datetime_str", "image"]
        missing_keys = [k for k in required_keys if k not in first_item]
        
        if missing_keys:
            print(f"ERROR: Missing keys in news item: {missing_keys}")
        else:
            print("SUCCESS: News item structure is valid.")
            
        # Check source diversity
        sources = set(n['source'] for n in news)
        print(f"Sources found: {sources}")
        
        # Print top 5 headlines
        print("\nTop 5 Headlines:")
        for n in news[:5]:
            print(f"- [{n['source']}] {n['datetime_str']}: {n['headline']}")
            
    except Exception as e:
        print(f"ERROR: Verification failed with exception: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_news_aggregation("AAPL")
    verify_news_aggregation("TSLA")
