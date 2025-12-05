from tradingview_screener import Query, Column

def test_financial_fields():
    print("Testing Financial Screener Fields...")
    
    candidates = [
        # Valuation
        'price_earnings_ttm', 'pe_ratio', # P/E
        'price_sales_ttm', # P/S
        'price_book_fq', # P/B
        'enterprise_value_ebitda_ttm', # EV/EBITDA
        'price_free_cash_flow_ttm', # P/FCF
        'dividend_yield_recent', # Div Yield
        
        # Income Statement
        'total_revenue_ttm', 'total_revenue_yoy_growth_ttm',
        'gross_profit_ttm', 'gross_margin_ttm',
        'operating_margin_ttm',
        'net_income_ttm', 'net_income_margin_ttm',
        'earnings_per_share_basic_ttm', 'earnings_per_share_diluted_ttm',
        
        # Balance Sheet
        'total_assets_fq', 
        'total_liabilities_fq',
        'total_debt_fq',
        'cash_n_equivalents_fq',
        'current_ratio_fq',
        'quick_ratio_fq',
        'debt_to_equity_fq',
        
        # Cash Flow
        'cash_flow_from_operating_activities_ttm',
        'free_cash_flow_ttm',
        
        # Returns
        'return_on_equity_ttm',
        'return_on_assets_ttm'
    ]
    
    found = []
    
    for field in candidates:
        try:
            # We use a known stock like AAPL to test
            q = Query().set_markets('america').select('name', field).where(Column('name') == 'AAPL')
            _, df = q.get_scanner_data()
            if not df.empty:
                val = df.iloc[0].get(field)
                print(f"✅ {field} exists. Value: {val}")
                found.append(field)
        except Exception as e:
            # print(f"❌ {field} failed") # formatted to reduce noise
            pass
            
    print(f"\nvalid_fields = {found}")

if __name__ == "__main__":
    test_financial_fields()
