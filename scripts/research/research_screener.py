from tradingview_screener import Query, Column
import pandas as pd

def test_screener():
    print("Testing TradingView Screener...")
    
    try:
        # Initialize the query
        q = Query().select(
            'name', 
            'close', 
            'change', 
            'market_cap_basic', 
            'volume', 
            'price_earnings_ttm', 
            'debt_to_equity_fq'
        )

        # Apply Filters
        # 1. Market Cap > $500 Million
        # 2. Change < -7%
        q = q.where(
            Column('market_cap_basic') > 500_000_000,
            Column('change') < -7
        )

        # Fetch Data
        count, df = q.get_scanner_data()
        
        print(f"Found {count} stocks.")

        # Clean up the view
        if not df.empty:
            # Sort by the biggest drop
            df = df.sort_values(by='change', ascending=True)
            
            # Rename columns for easier reading
            df = df.rename(columns={
                'price_earnings_ttm': 'P/E',
                'debt_to_equity_fq': 'Debt/Eq'
            })
            
            print(df.head())
            print("\nColumns:", df.columns.tolist())
            
            # Verify we have the data we need
            first_row = df.iloc[0]
            print("\nFirst row sample:")
            print(first_row)
            
        else:
            print("No stocks found matching criteria.")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_screener()
