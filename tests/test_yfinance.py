import yfinance as yf

print("Testing yfinance...")
try:
    tsla = yf.Ticker("TSLA")
    print(f"Price: {tsla.fast_info.last_price}")
except Exception as e:
    print(f"Error: {e}")
