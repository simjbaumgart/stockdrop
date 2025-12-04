from app.services.stock_service import stock_service
from app.services.tradingview_service import tradingview_service

def test_global_screener():
    print("Testing Global Screener Integration...")
    
    try:
        # Call the service directly to see the results
        movers = stock_service.get_large_cap_movers()
        
        print(f"\nFound {len(movers)} global movers:")
        
        # Group by region for better visibility
        by_region = {}
        
        for mover in movers:
            region = mover.get("region", "Unknown")
            if region not in by_region:
                by_region[region] = []
            by_region[region].append(mover)
            
        for region, stocks in by_region.items():
            print(f"\n--- {region} ({len(stocks)}) ---")
            for stock in stocks:
                print(f"{stock['symbol']}: {stock['change_percent']:.2f}% (Cap: {stock['market_cap']:,.0f} {stock.get('currency', '')})")
                
        if not movers:
            print("No movers found matching criteria (>$5B, <-4%, >50k vol).")
        else:
            print("\nVerification Successful!")
            
    except Exception as e:
        print(f"\nVerification Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_global_screener()
