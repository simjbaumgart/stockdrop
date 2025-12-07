from tradingview_screener import Query, Column
import pandas as pd

def check_currency():
    print("Checking Currency for Screener Results...")
    
    # We want to check 'currency' column if available
    # Let's try to select 'currency' and 'market_cap_basic'
    
    try:
        q = Query().select(
            'name', 
            'close', 
            'currency', 
            'market_cap_basic',
            'exchange'
        )

        # Let's look at some specific known stocks in different markets to verify
        # We can't easily filter by symbol list in screener query usually, 
        # but we can filter by exchange or just get a broad list and search.
        
        # Let's try to find a Chinese stock and a Japanese stock (if we were querying those markets)
        # Wait, currently the service defaults to 'america' screener implicitly if not specified?
        # The library defaults to 'america' usually.
        
        # Let's check what markets we are actually hitting.
        # If we are only hitting 'america', then everything should be USD.
        # But the user mentioned "chinese and japanese one".
        
        # Let's try to query specifically for a known Chinese stock (e.g. BABA - US listed, or 600519 - China listed)
        # and see what we get.
        
        # Actually, let's just fetch top results and see the currency column.
        
        count, df = q.get_scanner_data()
        
        if not df.empty:
            print(f"Fetched {len(df)} rows.")
            print(df[['name', 'currency', 'market_cap_basic', 'exchange']].head(10))
            
            currencies = df['currency'].unique()
            print(f"\nUnique Currencies found: {currencies}")
            
            # Check if we have any non-USD
            non_usd = df[df['currency'] != 'USD']
            if not non_usd.empty:
                print("\nNon-USD stocks found:")
                print(non_usd[['name', 'currency', 'market_cap_basic', 'exchange']].head())
            else:
                print("\nAll fetched stocks are in USD.")
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_currency()
