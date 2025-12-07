from app.services.tradingview_service import tradingview_service
import sys

def verify_indicators():
    print("--- Verifying Technical Indicators ---")
    
    # We'll use get_top_movers but with very lenient filters to ensure we get SOMETHING
    # actually get_top_movers uses global markets and concurrent execution.
    # It prints to stdout.
    
    # We want to inspect the 'cached_indicators' data structure.
    # So we will capture the return value.
    # High Market Cap to reduce result set size, but high enough to ensure data quality.
    
    print("Fetching top movers (limit to 1 item if possible logic allowed it, but will just take first result)...")
    
    # Using existing method
    movers = tradingview_service.get_top_movers(min_market_cap_usd=200_000_000_000, max_change_percent=1.0) 
    # Relaxed change percent to ensure we get results (positive or negative doesn't matter for *fields*)
    # Actually get_top_movers hardcodes < max_change_percent. 
    # Let's use 5.0 (positive) which means almost everything.
    
    if not movers:
        print("❌ No movers found to verify. Try relaxing filters in script.")
        return

    stock = movers[0]
    symbol = stock['symbol']
    print(f"\nVerifying data for: {symbol}")
    
    indicators = stock.get("cached_indicators", {})
    
    required_keys = ["adx", "cci", "vwma", "stoch_d", "stoch_k", "atr"]
    
    all_present = True
    for key in required_keys:
        val = indicators.get(key)
        if val is not None:
            print(f"✅ {key}: {val}")
        else:
            print(f"❌ {key}: MISSING")
            all_present = False
            
    if all_present:
        print("\nSUCCESS: All new indicators are being fetched correctly.")
    else:
        print("\nFAILURE: Some indicators are missing.")

if __name__ == "__main__":
    verify_indicators()
