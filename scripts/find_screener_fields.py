from tradingview_screener import Query

def search_fields(search_term):
    print(f"\nSearching for fields matching: '{search_term}'...")
    try:
        # Query() object has a 'get_scanner_data' method, but we want the list of valid columns.
        # The library usually documents them or we can inspect data.
        # However, a common trick is to query a wide range of plausible names if the library doesn't expose a list.
        # Wait, the library might pull the `columns` list dynamically. 
        # Let's inspect the `Column` class or similar if possible? 
        # Actually, let's try to just run a broad query for "America" and inspect the columns of the returned DataFrame? 
        # No, we have to request specific columns.
        
        # Better approach: The `tradingview_screener` library often validates columns against a list.
        # Let's try to access that internal list if possible, or just print potential matches if we can find them.
        
        # Let's try to access `Query.get_scanner_data` with a dummy column and see if it fails?
        # A more robust way: use the 'scan' functionality if available or just brute force common names.
        
        # WAIT: The library documentation or pypi page usually lists them.
        # Since I cannot browse the web freely for docs, I will inspect the library internals via code.
        
        import tradingview_screener
        # Usually checking `dir(tradingview_screener)` might reveal constants.
        
        # But wait, looking at `tradingview_service.py`, we see standard names.
        
        # Let's try to brute force 'ADX', 'CCI' etc to see if they are accepted.
        # If they are invalid, the API usually ignores them or errors.
        
        candidates = [
            "ADX", "ADX+DI", "ADX-DI", 
            "CCI20", "CCI", 
            "SuperTrend", "BBPower", "UO",
            "VWMA", "WMA",
            "AO", "Keltner.upper", "Keltner.lower",
            "Donchian.upper", "Donchian.lower",
            "Stoch.D", "Stoch.K", "Stoch.RSI.K",
            "ATR", "Volatility.D"
        ]
        
        valid_fields = []
        invalid_fields = []

        print(f"Testing {len(candidates)} candidates individually...")
        
        for field in candidates:
            try:
                # Minimal query
                q = Query().set_markets('america').select(field).limit(1)
                q.get_scanner_data()
                print(f"✅ {field}: VALID")
                valid_fields.append(field)
            except Exception as e:
                # The error message usually contains "Unknown field"
                if "Unknown field" in str(e):
                    print(f"❌ {field}: INVALID")
                else:
                    print(f"⚠️ {field}: ERROR ({str(e)[:50]}...)")
                invalid_fields.append(field)

        print("\n--- Summary ---")
        print("Valid:", valid_fields)

            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    search_fields("All Candidates")
