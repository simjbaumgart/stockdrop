import sys
import os
import pandas as pd
import numpy as np

# Add local repo to path to ensure we use the latest code
repo_path = os.path.join(os.getcwd(), 'defeatbeta-repo')
if os.path.exists(repo_path):
    sys.path.insert(0, repo_path)
    print(f"Added {repo_path} to sys.path")

try:
    from defeatbeta_api.data.ticker import Ticker
    import defeatbeta_api
    print(f"Imported defeatbeta_api from: {os.path.dirname(defeatbeta_api.__file__)}")
except ImportError as e:
    print(f"Error importing defeatbeta_api: {e}")
    sys.exit(1)

def main():
    ticker_symbol = 'GOOG'
    print(f"Attempting to fetch news for {ticker_symbol}...")

    try:
        ticker = Ticker(ticker_symbol)
        
        # Call the news method
        # Based on code: returns News(self.duckdb_client.query(sql))
        # We need to see what News object has. Likely a dataframe or list wrapper.
        news_obj = ticker.news()
        print(f"DEBUG: News object contents: {dir(news_obj)}")
        
        # Usually these wrapper objects have a method to get the dataframe or data
        # Let's inspect it or try common patterns like .to_dataframe() or .data
        # If it wraps a generic query result, maybe it behaves like a DF or has a property.
        
        # Let's try to print it directly to see if it has a nice repr
        print(f"News Object: {news_obj}")
        
    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
