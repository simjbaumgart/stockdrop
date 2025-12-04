from tradingview_ta import TA_Handler, Interval, Exchange

def test_indices():
    indices_to_test = [
        # S&P 500 attempts
        {"symbol": "SPX", "screener": "america", "exchange": "CBOE"},
        {"symbol": "SPX", "screener": "america", "exchange": "SP"},
        {"symbol": "US500", "screener": "cfd", "exchange": "TVC"},
        
        # STOXX 600 attempts
        {"symbol": "SXXP", "screener": "europe", "exchange": "TVC"},
        {"symbol": "SXXP", "screener": "europe", "exchange": "EUREX"},
        {"symbol": "SXXP", "screener": "europe", "exchange": "STOXX"}, # Retrying
        {"symbol": "STOXX", "screener": "europe", "exchange": "TVC"},
    ]

    print("Testing TradingView TA for Indices (Round 2)...")
    
    for index in indices_to_test:
        print(f"\nTesting {index['symbol']} on {index['exchange']} ({index['screener']})...")
        try:
            handler = TA_Handler(
                symbol=index["symbol"],
                screener=index["screener"],
                exchange=index["exchange"],
                interval=Interval.INTERVAL_1_DAY
            )
            analysis = handler.get_analysis()
            print(f"Success!")
            print(f"Price: {analysis.indicators['close']}")
        except Exception as e:
            print(f"Failed: {e}")

if __name__ == "__main__":
    test_indices()
