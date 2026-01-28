
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.benzinga_service import benzinga_service

def export_separated_news():
    tickers = ["SPY", "DIA", "QQQ"]
    results = {}
    
    print("Fetching top 10 news items for EACH ticker...")
    
    for ticker in tickers:
        print(f"Fetching {ticker}...")
        try:
            news = benzinga_service.get_company_news(ticker)
            # Sort and take top 10
            news.sort(key=lambda x: x.get('datetime', 0), reverse=True)
            results[ticker] = news[:10]
            print(f"  -> Got {len(results[ticker])} items.")
        except Exception as e:
            print(f"  -> Error: {e}")
            results[ticker] = []
            
    output_dir = "experiment_data"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "market_news_separated.json")
    
    with open(output_file, "w") as f:
        json.dump(results, f, indent=4)
        
    print(f"Exported separated news to {output_file}")

if __name__ == "__main__":
    export_separated_news()
