import yfinance as yf
import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta
import sys
import os

# Project root setup to allow imports if needed (though this script is mostly standalone)
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

def simulate_portfolio():
    print("--- Portfolio Performance Simulation ---")
    
    # 1. Define Tickers (S&P 100 subset + popular tech)
    tickers = [
        "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B", "LLY", "AVGO",
        "JPM", "V", "UNH", "WMT", "MA", "XOM", "JNJ", "PG", "HD", "COST",
        "ABBV", "MRK", "ORCL", "CRM", "AMD", "CVX", "NFLX", "PEP", "KO", "BAC",
        "TMO", "LIN", "WFC", "ADBE", "DIS", "MCD", "CSCO", "ACN", "ABT", "DHR",
        "INTC", "INTU", "VZ", "CMCSA", "QCOM", "TXN", "AMGN", "PFE", "IBM", "PM",
        "MS", "GE", "UNP", "CAT", "SPGI", "LOW", "UPS", "HON", "RTX", "BA",
        "BLK", "GS", "PLD", "SYK", "AMT", "ISRG", "BKNG", "T", "ELV", "TJX",
        "MDT", "AXP", "DE", "NEE", "LMT", "VRTX", "ADP", "MMC", "GILD", "BMY",
        "LRCX", "ADI", "MDLZ", "C", "MU", "SCHW", "TMUS", "CB", "REGN", "CI",
        "ETN", "BSX", "KLAC", "FI", "PANW", "SNPS", "EOG", "PGR", "CDNS", "SO"
    ]
    
    print(f"Universe size: {len(tickers)} stocks.")
    
    # 2. Batch Fetch Data
    # We need history for at least 1 year (365 days) + max horizon (365 days) = 2 years approx
    print("Fetching 2 years of historical data...")
    try:
        data = yf.download(tickers, period="2y", interval="1d", progress=True, group_by='ticker')
    except Exception as e:
        print(f"Error fetching data: {e}")
        return

    # Check if multi-level columns or single
    # yfinance structure varies by version. Usually MultiIndex (Ticker, PriceType)
    
    # 3. Simulate 100 Trades
    print("\nSimulating 100 random trades...")
    
    results = []
    horizons = [1, 3, 7, 14, 31, 180, 365]
    categories = ["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"]
    
    today = datetime.now()
    one_year_ago = today - timedelta(days=365)
    
    # Pre-process data into a more accessible dictionary
    # dict[ticker] -> DataFrame with 'Close'
    clean_data = {}
    
    if isinstance(data.columns, pd.MultiIndex):
        # Format: (PriceType, Ticker) or (Ticker, PriceType) depending on version/group_by
        # With group_by='ticker': top level is Ticker
        for ticker in tickers:
            try:
                # Extract close data
                df = data[ticker]
                clean_data[ticker] = df
            except KeyError:
                continue
    else:
        # Fallback if structure is different
        print("Data structure unexpected.")
        return

    for i in range(100):
        symbol = random.choice(tickers)
        
        # Pick random date within last year
        # days between 1 year ago and today
        days_range = (today - one_year_ago).days
        random_days = random.randint(0, days_range)
        buy_date = one_year_ago + timedelta(days=random_days)
        
        category = random.choices(categories, weights=[0.1, 0.3, 0.4, 0.1, 0.1])[0]
        
        # Get Stock Data
        stock_df = clean_data.get(symbol)
        if stock_df is None or stock_df.empty:
            continue
            
        # Find closest trading day to buy_date
        # Ensure we look forward if exact date missing
        buy_date_ts = pd.Timestamp(buy_date)
        
        # Verify index type
        if not isinstance(stock_df.index, pd.DatetimeIndex):
             # Try to convert
             stock_df.index = pd.to_datetime(stock_df.index)
        
        # Filter for dates >= buy_date
        future_df = stock_df[stock_df.index >= buy_date_ts]
        
        if future_df.empty:
            continue
            
        buy_row = future_df.iloc[0]
        actual_buy_date = future_df.index[0]
        buy_price = buy_row['Close']
        
        if pd.isna(buy_price):
            continue
            
        trade_record = {
            "Symbol": symbol,
            "Category": category,
            "Buy Date": actual_buy_date.date(),
            "Buy Price": buy_price
        }
        
        # Calculate ROI for horizons
        for days in horizons:
            target_date = actual_buy_date + timedelta(days=days)
            
            # Check if target is in future
            if target_date > pd.Timestamp(datetime.now()):
                trade_record[f"ROI_{days}d"] = np.nan # Future
            else:
                # Find price at or after target date
                horizon_df = stock_df[stock_df.index >= target_date]
                if horizon_df.empty:
                    # Maybe data ends? Use last available
                    sell_price = stock_df.iloc[-1]['Close']
                else:
                    sell_price = horizon_df.iloc[0]['Close']
                
                if pd.isna(sell_price):
                    trade_record[f"ROI_{days}d"] = np.nan
                else:
                    roi = ((sell_price - buy_price) / buy_price) * 100
                    trade_record[f"ROI_{days}d"] = roi
                    
        # Calculate Current/Today
        current_price = stock_df.iloc[-1]['Close']
        roi_current = ((current_price - buy_price) / buy_price) * 100
        trade_record["ROI_Current"] = roi_current
        
        results.append(trade_record)
        
    df_results = pd.DataFrame(results)
    
    print(f"Generated {len(df_results)} trades.")
    
    # 4. Aggregation and Summary
    print("\n\n--- Performance Summary by Category ---")
    
    # We want grouped stats for each horizon
    # Mean ROI, Median ROI, Win Rate
    
    for cat in categories:
        cat_df = df_results[df_results['Category'] == cat]
        if cat_df.empty:
            continue
            
        print(f"\nCategory: {cat.upper()} (n={len(cat_df)})")
        
        summary_rows = []
        
        horizon_cols = [f"ROI_{d}d" for d in horizons] + ["ROI_Current"]
        
        for col in horizon_cols:
            valid_stats = cat_df[col].dropna()
            if valid_stats.empty:
                continue
                
            mean_val = valid_stats.mean()
            median_val = valid_stats.median()
            win_rate = (valid_stats > 0).mean() * 100
            count = len(valid_stats)
            
            label = col.replace("ROI_", "")
            if label == "Current":
                label = "Today"
            else:
                label = label # e.g. 7d
            
            summary_rows.append({
                "Horizon": label,
                "Count": count,
                "Mean %": f"{mean_val:+.2f}",
                "Median %": f"{median_val:+.2f}",
                "Win Rate": f"{win_rate:.1f}%"
            })
            
        summary_df = pd.DataFrame(summary_rows)
        print(summary_df.to_string(index=False))

    print("\n\n--- Detailed Sample (First 10) ---")
    # Select a subset of columns for cleaner print
    display_cols = ["Symbol", "Category", "Buy Date", "Buy Price", "ROI_7d", "ROI_31d", "ROI_Current"]
    # Check if columns exist (might be NaN if simulating only 1 day)
    existing_cols = [c for c in display_cols if c in df_results.columns]
    
    # Format floats
    pd.set_option('display.float_format', '{:+.2f}'.format)
    print(df_results[existing_cols].head(10).to_string(index=False))


if __name__ == "__main__":
    simulate_portfolio()
