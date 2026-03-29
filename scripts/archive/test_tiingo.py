import requests
import json
import os

try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())
except ImportError:
    pass

TIINGO_API_KEY = os.getenv("TIINGO_API_KEY")
if not TIINGO_API_KEY:
    raise RuntimeError("TIINGO_API_KEY not set. Please set it in your .env file.")
BASE_URL = "https://api.tiingo.com/tiingo/news"

def fetch_news(tickers):
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Token {TIINGO_API_KEY}'
    }
    # Fetch news for the given tickers
    # Tiingo allows comma-separated tickers
    tickers_str = ",".join(tickers)
    url = f"{BASE_URL}?tickers={tickers_str}"
    
    print(f"Fetching news for: {tickers_str}")
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        articles = response.json()
        
        print(f"Found {len(articles)} articles.\n")
        
        for article in articles:
            print(f"Title: {article.get('title')}")
            print(f"Source: {article.get('source')}")
            print(f"Date: {article.get('publishedDate')}")
            print(f"URL: {article.get('url')}")
            print(f"Tickers: {article.get('tickers')}")
            print("-" * 50)
            
    except requests.exceptions.RequestException as e:
        print(f"Error fetching news: {e}")
        if e.response is not None:
             print(f"Error details: {e.response.text}")

if __name__ == "__main__":
    # Google (GOOG) and a random large cap Chinese company (Alibaba - BABA)
    tickers_to_check = ["GOOG", "BABA"]
    fetch_news(tickers_to_check)
