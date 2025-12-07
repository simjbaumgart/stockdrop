
import yfinance as yf
import finnhub
import os
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta

load_dotenv()

def test_yfinance_news(symbol):
    print(f"\nTesting yfinance news for {symbol}:")
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news
        if not news:
            print("No news found via yfinance.")
            return

        print(f"Found {len(news)} articles.")
        # Print first article to see structure
        if len(news) > 0:
            print("Sample Article Structure (Keys):", news[0].keys())
            print(json.dumps(news[0], indent=2))
    except Exception as e:
        print(f"Error testing yfinance: {e}")

def test_finnhub_news(symbol):
    print(f"\nTesting Finnhub news for {symbol}:")
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        print("FINNHUB_API_KEY not found.")
        return

    try:
        fh = finnhub.Client(api_key=api_key)
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        
        # company_news(symbol, _from, to)
        news = fh.company_news(symbol, _from=week_ago, to=today)
        
        print(f"Found {len(news)} articles (last 7 days).")
        if len(news) > 0:
            print("Sample Article Structure (Keys):", news[0].keys())
            print(json.dumps(news[0], indent=2))
            
    except Exception as e:
        print(f"Error testing Finnhub: {e}")

if __name__ == "__main__":
    symbol = "AAPL"
    test_yfinance_news(symbol)
    test_finnhub_news(symbol)
