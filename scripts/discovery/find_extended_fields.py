from tradingview_screener import Query, Column

def test_extended_fields():
    print("Testing Extended Screener Fields...")
    
    candidates = [
        # Performance
        'performance_1w', 'change|1W', 'Perf.W',
        'performance_1m', 'change|1M',
        'performance_3m', 
        'performance_6m',
        'performance_ytd',
        'performance_1y',
        'volatility_D', 'volatility_W', 'volatility_M',
        
        # Technicals - Rating
        'Recommend.All', 
        'Recommend.MA', 
        'Recommend.Other',
        
        # Technicals - Indicators
        'MACD.macd', 'MACD.signal',
        'Stoch.K', 'Stoch.D',
        'Mom', # Momentum
        'AO', # Awesome Oscillator
        'CCI20',
        'ADX'
    ]
    
    found = []
    
    for field in candidates:
        try:
            # We use a known stock like AAPL to test
            q = Query().set_markets('america').select('name', field).where(Column('name') == 'AAPL')
            _, df = q.get_scanner_data()
            if not df.empty:
                print(f"✅ {field} exists. Value: {df.iloc[0][field]}")
                found.append(field)
        except Exception as e:
            # print(f"❌ {field} failed") # formatted to reduce noise
            pass
            
    print(f"\nvalid_fields = {found}")

if __name__ == "__main__":
    test_extended_fields()
