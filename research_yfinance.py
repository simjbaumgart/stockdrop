import yfinance as yf
import json

def check_data():
    symbol = "TSLA"
    ticker = yf.Ticker(symbol)
    
    print(f"--- Checking {symbol} ---")
    
    # 1. Pre-market Data
    # fast_info often has 'last_price', 'previous_close', etc.
    # Sometimes pre-market is in 'info' dict under 'preMarketPrice'
    print("\n[Pre-market Data Search]")
    try:
        # Check fast_info first
        print("fast_info keys:", ticker.fast_info.keys())
        # Check info dict (this triggers a request)
        info = ticker.info
        print("Pre-market price in info:", info.get('preMarketPrice'))
        print("Current price in info:", info.get('currentPrice'))
    except Exception as e:
        print("Error checking pre-market:", e)

    # 2. Options Data
    print("\n[Options Data Search]")
    try:
        opts = ticker.options
        print(f"Expiration dates: {opts[:3]} ...")
        
        if opts:
            chain = ticker.option_chain(opts[0])
            print(f"Calls (first 2): \n{chain.calls.head(2)}")
            print(f"Puts (first 2): \n{chain.puts.head(2)}")
    except Exception as e:
        print("Error checking options:", e)

if __name__ == "__main__":
    check_data()
