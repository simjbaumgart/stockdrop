from tradingview_screener import Query, Column

def test_fields_individually():
    print("Testing fields individually...")
    
    candidates = [
        'RSI', 
        'SMA200', 
        'BollingerBandsLower20', 
        'BollingerBandsUpper20',
        'BB.lower', 
        'BB.upper'
    ]
    
    found = []
    
    for field in candidates:
        try:
            q = Query().set_markets('america').select('name', field).where(Column('name') == 'AAPL')
            q.get_scanner_data() # If this doesn't raise, field exists
            print(f"✅ {field} exists")
            found.append(field)
        except Exception as e:
            # print(f"❌ {field} failed: {e}")
            pass
            
    print(f"\nValid fields found: {found}")

if __name__ == "__main__":
    test_fields_individually()
