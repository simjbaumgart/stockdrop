import sqlite3
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import os
import matplotlib.pyplot as plt
import seaborn as sns
import pytz

DB_NAME = os.getenv("DB_PATH", "subscribers.db")

def get_buy_decisions():
    conn = sqlite3.connect(DB_NAME)
    query = """
        SELECT 
            id, symbol, price_at_decision, 
            recommendation, IFNULL(deep_research_verdict, 'None') as deep_research_verdict, 
            timestamp
        FROM decision_points 
        WHERE recommendation = 'BUY' 
          AND timestamp >= '2026-01-15 00:00:00'
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

def fetch_14_day_history(symbol, start_date_str):
    """Fetch 15 days of data to ensure we capture a full 14 calendar days."""
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d %H:%M:%S")
    end_dt = start_dt + timedelta(days=15)
    
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")
    
    ticker = yf.Ticker(symbol)
    history = ticker.history(start=start_str, end=end_str)
    
    if history.empty:
        return None
        
    return history

import textwrap

def process_and_plot():
    df = get_buy_decisions()
    if df.empty:
        print("No BUY recommendations found since Jan 15, 2026.")
        return

    out_dir = "reports/2_week_performance"
    os.makedirs(out_dir, exist_ok=True)
    
    print(f"Found {len(df)} BUY recommendations. Generating plots...")
    
    for _, row in df.iterrows():
        symbol = row['symbol']
        verdict = row['deep_research_verdict']
        timestamp = row['timestamp']
        
        stock_hist = fetch_14_day_history(symbol, timestamp)
        sp500_hist = fetch_14_day_history("^GSPC", timestamp)
        
        if stock_hist is None or sp500_hist is None or stock_hist.empty or sp500_hist.empty:
            print(f"[{symbol}] Incomplete data. Skipping.")
            continue
            
        # Limit strictly to 14 days from timestamp
        start_dt_tz_naive = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
        end_dt_tz_naive = start_dt_tz_naive + timedelta(days=14)
        
        tz = stock_hist.index.tzinfo
        if tz:
            start_dt_aware = pd.to_datetime(start_dt_tz_naive).tz_localize(tz)
            end_dt_aware = pd.to_datetime(end_dt_tz_naive).tz_localize(tz)
        else:
            start_dt_aware = pd.to_datetime(start_dt_tz_naive)
            end_dt_aware = pd.to_datetime(end_dt_tz_naive)
            
        stock_hist = stock_hist[(stock_hist.index >= start_dt_aware) & (stock_hist.index <= end_dt_aware)]
        sp500_hist = sp500_hist[(sp500_hist.index >= start_dt_aware) & (sp500_hist.index <= end_dt_aware)]
        
        if stock_hist.empty or sp500_hist.empty:
            print(f"[{symbol}] No valid trading days found in the 14-day window. Skipping.")
            continue
            
        # Align on dates
        stock_df = stock_hist[['Close']].copy()
        stock_df.index = stock_df.index.normalize()
        stock_df = stock_df.rename(columns={'Close': 'stock_close'})
        
        sp500_df = sp500_hist[['Close']].copy()
        sp500_df.index = sp500_df.index.normalize()
        sp500_df = sp500_df.rename(columns={'Close': 'sp500_close'})
        
        merged = stock_df.join(sp500_df, how='inner')
        if merged.empty:
            print(f"[{symbol}] Failed to align dates. Skipping.")
            continue
            
        stock_base = merged['stock_close'].iloc[0]
        sp500_base = merged['sp500_close'].iloc[0]
        
        merged['stock_roi'] = ((merged['stock_close'] - stock_base) / stock_base) * 100
        merged['sp500_roi'] = ((merged['sp500_close'] - sp500_base) / sp500_base) * 100
        
        plt.figure(figsize=(10, 6))
        plt.plot(merged.index, merged['stock_roi'], marker='o', label=f'{symbol} ROI', color='blue')
        plt.plot(merged.index, merged['sp500_roi'], marker='x', label='S&P 500 ROI', color='black', linestyle='--')
        
        plt.axhline(0, color='gray', linestyle='-', alpha=0.5)
        
        date_str = start_dt_tz_naive.strftime("%Y-%m-%d")
        wrapped_verdict = "\n".join(textwrap.wrap(verdict, width=80))
        
        title = f"{symbol} vs S&P 500 (14 Days Post-Recommendation: {date_str})"
        plt.title(title, fontsize=14, pad=20)
        
        # Add a text box for the Deep Research Verdict
        props = dict(boxstyle='round', facecolor='wheat', alpha=0.3)
        plt.figtext(0.5, 0.01, f"Verdict: {wrapped_verdict}", wrap=True, horizontalalignment='center', fontsize=10, bbox=props)
        
        # Adjust layout to make room for text at bottom
        plt.subplots_adjust(bottom=0.25)
        
        plt.xlabel("Date")
        plt.ylabel("Cumulative ROI (%)")
        plt.legend()
        plt.grid(True, alpha=0.3)
        
        safe_date = date_str.replace("-", "")
        filename = f"{out_dir}/{symbol}_{safe_date}_14d_perf.png"
        plt.savefig(filename)
        plt.close()
        print(f"[{symbol}] Generated {filename}")

if __name__ == "__main__":
    process_and_plot()
