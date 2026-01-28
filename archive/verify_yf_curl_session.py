
import yfinance as yf
from curl_cffi import requests

try:
    session = requests.Session()
    t = yf.Ticker("AAPL", session=session)
    # Trigger a call
    print(f"Info keys: {list(t.info.keys())[:5]}")
    print("Success: yf.Ticker accepts curl_cffi Session.")
except TypeError as e:
    print(f"Failure: {e}")
except Exception as e:
    print(f"Error: {e}")
