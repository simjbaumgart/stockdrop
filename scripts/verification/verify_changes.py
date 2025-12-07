import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.services.gatekeeper_service import gatekeeper_service
from app.services.stock_service import stock_service
from app.services.tradingview_service import tradingview_service

def verify_changes():
    print("--- Verifying TradingView Replacement (Global) ---")
    
    # 1. Gatekeeper Market Regime
    print("\n1. Testing Gatekeeper Market Regime (SPY)...")
    try:
        regime = gatekeeper_service.check_market_regime()
        print(f"Result: {regime}")
        if regime['regime'] in ['BULL', 'BEAR']:
            print("SUCCESS: Market Regime check passed.")
        else:
            print("FAILURE: Market Regime returned UNKNOWN or unexpected.")
    except Exception as e:
        print(f"FAILURE: Market Regime check raised exception: {e}")

    # 2. Gatekeeper Technical Filters
    print("\n2. Testing Gatekeeper Technical Filters (AAPL & Non-US)...")
    try:
        # Test US
        print(" -> Checking AAPL (US)...")
        is_valid, reasons = gatekeeper_service.check_technical_filters("AAPL", region="US", exchange="NASDAQ", screener="america")
        print(f"AAPL Is Valid: {is_valid}")
        print(f"AAPL Reasons: {reasons.keys()}")
        
        # Test Non-US (France - OVH)
        # Note: We need correct exchange/screener. "EURONEXT" is common for France.
        print(" -> Checking OVH (France)...")
        is_valid_ovh, reasons_ovh = gatekeeper_service.check_technical_filters("OVH", region="Europe (France)", exchange="EURONEXT", screener="france")
        print(f"OVH Is Valid: {is_valid_ovh}")
        print(f"OVH Reasons: {reasons_ovh.keys()}")
        if 'error' in reasons_ovh:
            print(f"OVH Error (Expected if exchange mismatch): {reasons_ovh['error']}")

        # Test Non-US (Japan - 4704)
        print(" -> Checking 4704 (Japan)...")
        is_valid_jp, reasons_jp = gatekeeper_service.check_technical_filters("4704", region="Japan", exchange="TSE", screener="japan")
        print(f"4704 Is Valid: {is_valid_jp}") 
        
        if 'bb_status' in reasons:
            print("SUCCESS: Technical Filters check passed (at least for AAPL).")
            
        print("\n -> Testing Cached Data Logic...")
        # Simulate cached data for AAPL
        cached = {
            "close": 200.0,
            "sma200": 150.0,
            "rsi": 30.0, # Oversold
            "bb_lower": 190.0,
            "bb_upper": 210.0,
            "volume": 1000000
        }
        # Should be valid (Deep Dip: price 200 > bb_lower 190? No, wait. 
        # %B = (200 - 190) / (210 - 190) = 10 / 20 = 0.5. 
        # Deep Dip requires < 0.5. So exactly 0.5 is REJECTED (Not Dip Enough).
        
        # Let's try 180 (Price < Lower 190) -> %B < 0
        cached["close"] = 180.0
        # %B = (180 - 190) / 20 = -0.5 < 0.5 -> VALID
        
        is_valid_cache, reasons_cache = gatekeeper_service.check_technical_filters("AAPL", cached_indicators=cached)
        print(f"Cached Is Valid: {is_valid_cache}")
        print(f"Cached Reasons: {reasons_cache}")
        
        if is_valid_cache and 'bb_status' in reasons_cache:
            print("SUCCESS: Cached data usage verified.")
        else:
            print("FAILURE: Cached data usage failed.")

    except Exception as e:
        print(f"FAILURE: Technical Filters check raised exception: {e}")

if __name__ == "__main__":
    verify_changes()
