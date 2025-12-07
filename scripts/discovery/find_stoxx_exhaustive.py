from tradingview_ta import TA_Handler, Interval, Exchange

def find_stoxx600_exhaustive():
    print("Exhaustive Search for STOXX 600...")
    
    # Combinations to try
    candidates = [
        {"symbol": "STOXX", "screener": "europe", "exchange": "TVC"},
        {"symbol": "STOXX", "screener": "europe", "exchange": "INDEX"},
        {"symbol": "SXXP", "screener": "europe", "exchange": "TVC"}, # Retrying
        {"symbol": "SXXP", "screener": "europe", "exchange": "INDEX"},
        {"symbol": "STOXX600", "screener": "europe", "exchange": "TVC"},
        {"symbol": "STOXX600", "screener": "europe", "exchange": "INDEX"},
        {"symbol": "SXXP", "screener": "germany", "exchange": "XETRA"},
        {"symbol": "SXXP", "screener": "germany", "exchange": "EURONEXT"},
        {"symbol": "0O1C", "screener": "uk", "exchange": "LSE"}, # Sometimes listed as ETF/Index
    ]
    
    for cand in candidates:
        try:
            print(f"Trying {cand}...")
            handler = TA_Handler(
                symbol=cand["symbol"],
                screener=cand["screener"],
                exchange=cand["exchange"],
                interval=Interval.INTERVAL_1_DAY
            )
            analysis = handler.get_analysis()
            if analysis:
                print(f"SUCCESS! Found STOXX 600: {cand}")
                print(f"Price: {analysis.indicators['close']}")
                return
        except Exception as e:
            print(f"Failed: {e}")

if __name__ == "__main__":
    find_stoxx600_exhaustive()
