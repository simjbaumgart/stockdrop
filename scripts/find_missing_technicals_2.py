from tradingview_screener import Query, Column

def test_fields_2():
    print("Testing More Fields...")
    
    candidates = [
        # 52 Week
        'price_52_week_high', 'price_52_week_low',
        'High.All', 'Low.All',
        '52_week_high', '52_week_low',
        
        # Short Info (often not in screener, but let's try variants)
        'short_interest', 'short_float',
        
        # OBV might be 'OBV' or 'OnBalanceVolume'
        'OnBalanceVolume'
    ]
    
    for field in candidates:
        try:
            q = Query().set_markets('america').select('name', field).where(Column('name') == 'AAPL')
            _, df = q.get_scanner_data()
            if not df.empty:
                val = df.iloc[0].get(field)
                print(f"✅ {field} exists. Value: {val}")
        except Exception:
            pass

if __name__ == "__main__":
    test_fields_2()
