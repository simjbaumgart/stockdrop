
import yfinance as yf
import json

def debug_yf():
    ticker = yf.Ticker("AAPL")
    news = ticker.news
    print(f"Count: {len(news)}")
    if news:
        print(json.dumps(news[0], indent=2))
        
        # Check timestamps
        for i, n in enumerate(news[:5]):
            print(f"Item {i}: pubTime={n.get('providerPublishTime')} title={n.get('title')}")

if __name__ == "__main__":
    debug_yf()
