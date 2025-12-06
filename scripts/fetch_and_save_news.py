
import sys
import os
import json
from datetime import datetime

# Add project root to sys.path
sys.path.append(os.getcwd())

from app.services.stock_service import stock_service

def fetch_and_save_news(symbols):
    save_dir = "data/news"
    os.makedirs(save_dir, exist_ok=True)
    
    for symbol in symbols:
        print(f"\nFetching news for {symbol}...")
        try:
            news = stock_service.get_aggregated_news(symbol)
            print(f"Found {len(news)} items for {symbol}.")
            
            if news:
                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                filename = f"{save_dir}/{symbol}_news_v2_{timestamp}.json"
                
                with open(filename, 'w') as f:
                    json.dump(news, f, indent=2)
                
                print(f"Saved news to {filename}")
            else:
                print(f"No news found for {symbol}.")
                
        except Exception as e:
            print(f"Error processing {symbol}: {e}")

if __name__ == "__main__":
    symbols = ["PSN", "DOCS", "XP"]
    fetch_and_save_news(symbols)
