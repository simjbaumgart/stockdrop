from tradingview_screener import Query, Column

def test_fields():
    print("Testing Missing Technical Fields...")
    
    candidates = [
        # 1. Raw Price (daily)
        'open', 'high', 'low', 
        
        # 2. Trend
        'SMA50', 
        'MACD.hist', # Histogram?
        
        # 3. Momentum
        # Stoch.D already found previously?
        
        # 4. Volatility
        'ATR', 'AverageTrueRange', 
        
        # 5. Volume Context
        'relative_volume_10d_calc', 'RVOL',
        'OBV', # On Balance Volume? Unlikely in screener but let's check
        
        # 6. Key Levels & Structure
        'high_52_week', 'low_52_week',
        'beta_1_year', 'beta_5_year',
        'float_shares_outstanding', # For calculation?
        'short_interest_share', # Short %?
    ]
    
    for field in candidates:
        try:
            q = Query().set_markets('america').select('name', field).where(Column('name') == 'AAPL')
            _, df = q.get_scanner_data()
            if not df.empty:
                val = df.iloc[0].get(field)
                print(f"✅ {field} exists. Value: {val}")
            else:
                print(f"⚠️ {field} returned empty")
        except Exception as e:
            # print(f"❌ {field} failed")
            pass

if __name__ == "__main__":
    test_fields()
