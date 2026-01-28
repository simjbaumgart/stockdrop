
import sqlite3
import pandas as pd
import yfinance as yf
from datetime import datetime
import os

# Configuration
DB_PATH = "subscribers.db" 
if not os.path.exists(DB_PATH):
    # Try typical location
    DB_PATH = "/Users/simonbaumgart/Antigravity/Stock-Tracker/subscribers.db"

print(f"Using Database: {DB_PATH}")

def load_decisions(db_path):
    try:
        conn = sqlite3.connect(db_path)
        # Check if table exists
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='decision_points'")
        if not cursor.fetchone():
            print("Table decision_points does not exist.")
            return pd.DataFrame()

        query = """
        SELECT 
            id, symbol, price_at_decision, drop_percent, recommendation, 
            reasoning, status, timestamp, ai_score, 
            deep_research_verdict, deep_research_score
        FROM decision_points
        ORDER BY timestamp ASC
        LIMIT 5
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        print(f"DB Error: {e}")
        return pd.DataFrame()

# 1. Load Data
print("Loading data...")
df = load_decisions(DB_PATH)

if df.empty:
    print("No data found or empty table. Verification stopped (but DB connection might be ok).")
    exit(0)

print(f"Loaded {len(df)} rows for testing.")

# Preprocessing
df['timestamp'] = pd.to_datetime(df['timestamp'])
df['date'] = df['timestamp'].dt.date

# 2. Test YFinance
row = df.iloc[0]
symbol = row['symbol']
start_date = row['date']

print(f"Testing yfinance for {symbol} since {start_date}...")
try:
    ticker = yf.Ticker(symbol)
    start_str = start_date.strftime('%Y-%m-%d')
    hist = ticker.history(start=start_str)
    
    if not hist.empty:
        print(f"Success! Fetched {len(hist)} days of data.")
        latest = hist.iloc[-1]['Close']
        print(f"Latest Price: {latest}")
    else:
        print("Warning: yfinance returned empty data (might be a test symbol or delisted).")
except Exception as e:
    print(f"yfinance failed: {e}")

print("Verification complete.")
