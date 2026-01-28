
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.benzinga_service import benzinga_service

def verify_dax_news():
    # Tickers to try
    # DAX: The index itself (often problematic on some US APIs)
    # EWG: iShares MSCI Germany ETF (US traded proxy)
    # ^GDAXI: Yahoo style
    candidates = ["DAX", "EWG", "^GDAXI", "DX-Y.NYB"] # DX-Y is dollar index, just testing format
    
    for ticker in candidates:
        print(f"\n--- Testing News for {ticker} ---")
        try:
            news = benzinga_service.get_company_news(ticker)
            print(f"Found {len(news)} articles.")
            if news:
                print(f"First article headline: {news[0]['headline']}")
                print(f"First article summary: {news[0]['summary'][:100]}...")
        except Exception as e:
            print(f"Error fetching: {e}")

if __name__ == "__main__":
    verify_dax_news()
