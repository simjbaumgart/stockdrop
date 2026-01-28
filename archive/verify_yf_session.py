
import yfinance as yf
import requests

try:
    session = requests.Session()
    t = yf.Ticker("AAPL", session=session)
    print("Success: yf.Ticker accepts session.")
except TypeError as e:
    print(f"Failure: {e}")
except Exception as e:
    print(f"Error: {e}")
