
import sys
import os
import json
from dotenv import load_dotenv
import finnhub

load_dotenv()

def test_filings_structure(symbol):
    print(f"Fetching filings for {symbol}...")
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        print("No API Key found")
        return

    fh = finnhub.Client(api_key=api_key)
    
    # 1. Get List
    try:
        # Get filings from last 3 months
        filings = fh.filings(symbol=symbol, _from="2025-01-01", to="2025-12-06")
        if not filings:
            print("No filings found.")
            return
            
        print(f"Found {len(filings)} filings.")
        print("First filing keys:", filings[0].keys())
        
        # Find an 8-K or 10-Q
        target_filing = None
        for f in filings:
            if f['form'] in ['10-K', '10-Q', '8-K']:
                target_filing = f
                break
                
        if target_filing:
             print(f"Testing extraction for {target_filing['form']} at {target_filing['filingUrl']}")
             # We need to use the FinnhubService extraction logic or simple requests to test
             # Let's mock a simple extraction request here to verify content
             import requests
             headers = {"User-Agent": "Bot"}
             # The URL in finnhub response is usually restricted or needs specific handling?
             # Finnhub returns a URL to the filing on their site or SEC?
             # Usually 'filingUrl': 'https://www.sec.gov/Archives/edgar/data/...'
             url = target_filing['filingUrl']
             report_url = target_filing['reportUrl']
             print(f"Index URL: {url}")
             print(f"Report URL: {report_url}")
        else:
             print("No 10-K/10-Q/8-K found.")
             
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_filings_structure("AAPL")
