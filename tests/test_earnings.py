import yfinance as yf
from datetime import datetime

def check_earnings(symbol):
    print(f"Checking earnings for {symbol}...")
    ticker = yf.Ticker(symbol)
    
    try:
        # Method 1: calendar
        cal = ticker.calendar
        print(f"Calendar: {cal}")
        
        # Method 2: earnings_dates
        earnings_dates = ticker.earnings_dates
        print(f"Earnings Dates: {earnings_dates}")
        
        # Method 3: news (might mention earnings)
        # news = ticker.news
        # print(f"News: {news[:1]}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_earnings("NVDA")
    check_earnings("AAPL")
