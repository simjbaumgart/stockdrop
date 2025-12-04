from tradingview_screener import Query, Column

def search_stoxx():
    print("Searching for STOXX 600 variants...")
    
    # We'll search in the 'europe' market first, but also 'america' (for CFDs)
    markets_to_try = ['europe', 'germany', 'america', 'cfd']
    
    for market in markets_to_try:
        print(f"\n--- Searching in '{market}' ---")
        try:
            # We can't easily "search" by text in the query builder directly like a search bar,
            # but we can filter by description or name containing string if supported,
            # OR we just fetch a bunch of indices/CFDs and filter locally.
            
            # TradingView Screener library doesn't support "contains" easily in all versions.
            # Let's try to fetch top indices/CFDs and look for "STOXX".
            
            # Note: 'cfd' might not be a valid "market" string for set_markets in the library, 
            # usually it's a screener type. The library defaults to stock screener.
            # For indices, we might need a different scanner class or just try standard markets.
            
            q = Query().set_markets(market).select('name', 'description', 'exchange', 'close', 'type')
            
            # Let's try to get a lot of rows and filter python-side
            count, df = q.get_scanner_data()
            
            if not df.empty:
                # Filter for STOXX
                stoxx_matches = df[df['description'].str.contains('STOXX', case=False, na=False) | 
                                   df['name'].str.contains('STOXX', case=False, na=False) |
                                   df['name'].str.contains('SXXP', case=False, na=False)]
                
                if not stoxx_matches.empty:
                    print(f"Found {len(stoxx_matches)} matches:")
                    print(stoxx_matches[['name', 'description', 'exchange', 'type', 'close']].head(20))
                else:
                    print("No 'STOXX' matches found in top results.")
            else:
                print("No data returned.")
                
        except Exception as e:
            print(f"Error searching {market}: {e}")

if __name__ == "__main__":
    search_stoxx()
