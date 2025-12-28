
from tradingview_ta import TA_Handler, Interval, Exchange

def test_nifty_lookup():
    print("Testing TradingView Lookup for Nifty 50...")
    
    # Try NIFTY
    try:
        print("\nAttempt 1: Symbol='NIFTY', Exchange='NSE', Screener='india'")
        handler = TA_Handler(
            symbol="NIFTY",
            screener="india",
            exchange="NSE",
            interval=Interval.INTERVAL_1_DAY
        )
        analysis = handler.get_analysis()
        print(f"Success! Price: {analysis.indicators['close']}")
        return
    except Exception as e:
        print(f"Failed: {e}")

    # Try NIFTY50
    try:
        print("\nAttempt 2: Symbol='NIFTY50', Exchange='NSE', Screener='india'")
        handler = TA_Handler(
            symbol="NIFTY50",
            screener="india",
            exchange="NSE",
            interval=Interval.INTERVAL_1_DAY
        )
        analysis = handler.get_analysis()
        print(f"Success! Price: {analysis.indicators['close']}")
        return
    except Exception as e:
        print(f"Failed: {e}")
        
    # Try ^NSEI (Current - Expected Fail)
    try:
        print("\nAttempt 3: Symbol='^NSEI', Exchange='NSE', Screener='india'")
        handler = TA_Handler(
            symbol="^NSEI",
            screener="india",
            exchange="NSE",
            interval=Interval.INTERVAL_1_DAY
        )
        analysis = handler.get_analysis()
        print(f"Success! Price: {analysis.indicators['close']}")
    except Exception as e:
        print(f"Failed (Expected): {e}")

if __name__ == "__main__":
    test_nifty_lookup()
