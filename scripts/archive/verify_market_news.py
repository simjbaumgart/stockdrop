
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.benzinga_service import benzinga_service

def verify_market_news():
    tickers = ["SPY", "QQQ", "DIA"]
    
    for ticker in tickers:
        print(f"\n--- Testing News for {ticker} ---")
        news = benzinga_service.get_company_news(ticker)
        print(f"Found {len(news)} articles.")
        if news:
            print(f"First article headline: {news[0]['headline']}")
            print(f"First article summary: {news[0]['summary'][:100]}...")

if __name__ == "__main__":
    verify_market_news()
