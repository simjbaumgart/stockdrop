
import yfinance as yf
import time
import threading
import os
import psutil

def get_process_info():
    process = psutil.Process(os.getpid())
    return {
        "files": process.num_fds() if hasattr(process, 'num_fds') else "N/A",
        "threads": process.num_threads()
    }

print(f"Initial State: {get_process_info()}")

tickers = ["AAPL", "MSFT", "GOOG", "AMZN", "TSLA", "NVDA", "META", "NFLX"] * 100 # 800 tickers

def fetch_ticker(symbol):
    try:
        # Mimic StockService behavior: create new Ticker object
        t = yf.Ticker(symbol)
        # Access property that triggers network request
        _ = t.news
        # _ = t.info # Info is heavier, news is lighter but still hits endpoint
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")

start_time = time.time()

# Sequential Loop (like StockService check_large_cap_drops)
for i, sym in enumerate(tickers[:200]): # Try 200 first
    fetch_ticker(sym)
    if i % 10 == 0:
        print(f"Processed {i} tickers. State: {get_process_info()}")

print(f"Finished Sequential. State: {get_process_info()}")
