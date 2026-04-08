import sqlite3
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

DB_PATH = "subscribers.db"

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def fetch_data():
    conn = get_db_connection()
    query = """
    SELECT 
        id, timestamp, symbol, region, ai_score, recommendation, 
        price_at_decision, deep_research_verdict, batch_id, deep_research_score, batch_winner
    FROM decision_points
    WHERE timestamp >= date('now', '-30 days') -- Optimization: Last 30 days
    ORDER BY timestamp DESC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

from app.services.yahoo_ticker_resolver import YahooTickerResolver
import contextlib
import io

@contextlib.contextmanager
def suppress_output():
    """Context manager to suppress stdout and stderr."""
    save_stdout = sys.stdout
    save_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = save_stdout
        sys.stderr = save_stderr

def fetch_yfinance_data(symbols, indices_tickers=['^GSPC', '^DJI', '^GDAXI']):
    resolver = YahooTickerResolver()
    
    # Resolve Symbols
    clean_symbols = []
    symbol_map = {} # Original -> Resolved
    
    print(f"Resolving {len(symbols)} tickers...")
    for s in symbols:
        # Use simple region heuristic if available in DB (passed later? No, currently raw symbols)
        # We can try to guess based on known patterns or just rely on resolver defaults
        resolved = resolver.resolve(s)
        clean_symbols.append(resolved)
        symbol_map[s] = resolved
        
    all_tickers = list(set(clean_symbols + indices_tickers))
    
    print(f"Fetching data for {len(all_tickers)} unique tickers...")
    
    # Download data
    # Suppress YFinance noise
    with suppress_output():
        data = yf.download(all_tickers, period="3mo", interval="1d", progress=False)['Close']
    
    # If single ticker result, it's a Series, convert to DF
    if isinstance(data, pd.Series):
        data = data.to_frame()
        
    # Map back? The DataFrame columns will be the RESOLVED tickers.
    # When we query, we need to query by resolved ticker.
    # So we return (data, symbol_map)
    return data, symbol_map

def get_price_on_date(price_data, ticker, date_str):
    try:
        if ticker not in price_data.columns:
            return None
            
        target_date = pd.to_datetime(date_str).tz_localize(None)
        
        idx = price_data.index.tz_localize(None)
        
        # Check if date exists
        if target_date in idx:
            return price_data.loc[target_date, ticker]
        
        # Find next valid date
        # Assuming sorted index
        future_dates = idx[idx > target_date]
        if not future_dates.empty:
             return price_data.loc[future_dates[0], ticker]
             
        return None
    except Exception as e:
        return None

def calculate_change(start_price, end_price):
    if start_price is not None and end_price is not None and start_price != 0:
        return (end_price - start_price) / start_price
    return None

def generate_report():
    df = fetch_data()
    
    # 1. Pre-process Dates
    df['date_obj'] = pd.to_datetime(df['timestamp'])
    df['date_str'] = df['date_obj'].dt.strftime('%Y-%m-%d')
    
    # 2. De-duplication
    # Strategy: Group by Symbol + date_str.
    # Prioritize: 
    #   1. deep_research_verdict is NOT NULL and NOT ''
    #   2. Higher ai_score
    
    def rank_row(row):
        score = row['ai_score'] if row['ai_score'] else 0
        has_deep = 1 if row['deep_research_verdict'] and row['deep_research_verdict'] not in ['-', '', 'PENDING', None] else 0
        return (has_deep, score)

    df['rank'] = df.apply(rank_row, axis=1)
    df = df.sort_values(by=['symbol', 'date_str', 'rank'], ascending=[True, True, False])
    
    # Drop duplicates, keeping the first (highest rank)
    df_dedup = df.drop_duplicates(subset=['symbol', 'date_str'], keep='first').copy()
    
    print(f"Raw rows: {len(df)}. Deduped rows: {len(df_dedup)}")
    
    # 3. Prepare for YFinance
    symbols = df_dedup['symbol'].unique().tolist()
    
    # We need to use the region info for better resolution
    # Create a richer text for resolver? 
    # The current resolver.resolve takes (symbol, exchange, name, region)
    # But fetch_yfinance_data just takes symbols list.
    # Let's modify fetch_yfinance_data to take the whole DF or a detailed list?
    # Or just use the simple resolver loop inside fetch_yfinance_data. 
    # Actually, we can pass region inside the loop if we refactor, but for now let's just use symbol.
    
    price_data, symbol_map = fetch_yfinance_data(symbols)
    
    # 4. Build Report Columns
    report_rows = []
    
    indices = {
        'SP500': '^GSPC',
        'Dow': '^DJI',
        'DAX': '^GDAXI'
    }
    
    for index, row in df_dedup.iterrows():
        # Core Info
        date_str = row['date_str']
        symbol = row['symbol']
        resolved_ticker = symbol_map.get(symbol, symbol)

        # Calculate Target Dates
        start_date = row['date_obj']
        end_date_1w = start_date + timedelta(days=7)
        end_date_2w = start_date + timedelta(days=14)
        end_date_4w = start_date + timedelta(days=28)

        # Prices
        price_dec = row['price_at_decision']
        now = datetime.now()

        # --- 1W ---
        price_1w = get_price_on_date(price_data, resolved_ticker, end_date_1w.strftime('%Y-%m-%d'))
        if now < end_date_1w:
            price_1w_disp, perf_1w_disp = "-", "-"
        else:
            price_1w_disp = f"{price_1w:.2f}" if price_1w is not None else "-"
            change_1w = calculate_change(price_dec, price_1w)
            perf_1w_disp = f"{change_1w*100:+.2f}%" if change_1w is not None else "-"

        # --- 2W ---
        price_2w = get_price_on_date(price_data, resolved_ticker, end_date_2w.strftime('%Y-%m-%d'))
        if now < end_date_2w:
            price_2w_disp, perf_2w_disp = "-", "-"
        else:
            price_2w_disp = f"{price_2w:.2f}" if price_2w is not None else "-"
            change_2w = calculate_change(price_dec, price_2w)
            perf_2w_disp = f"{change_2w*100:+.2f}%" if change_2w is not None else "-"

        # --- 4W ---
        price_4w = get_price_on_date(price_data, resolved_ticker, end_date_4w.strftime('%Y-%m-%d'))
        if now < end_date_4w:
            price_4w_disp, perf_4w_disp = "-", "-"
        else:
            price_4w_disp = f"{price_4w:.2f}" if price_4w is not None else "-"
            change_4w = calculate_change(price_dec, price_4w)
            perf_4w_disp = f"{change_4w*100:+.2f}%" if change_4w is not None else "-"

        # Status (based on longest horizon)
        if now < end_date_1w:
            status = "Pending (<1w)"
        elif now < end_date_2w:
            status = "Partial (1w)"
        elif now < end_date_4w:
            status = "Partial (2w)"
        else:
            status = "Completed" if price_4w is not None else "Data Missing"

        # Benchmark Performance (+1W only, for brevity)
        bench_res = {}
        for name, ticker in indices.items():
            start_p = get_price_on_date(price_data, ticker, date_str)
            end_p = get_price_on_date(price_data, ticker, end_date_1w.strftime('%Y-%m-%d'))
            chg = calculate_change(start_p, end_p)
            bench_res[name] = f"{chg*100:+.2f}%" if chg is not None else "+0.00%"

        # Verdict / Rec
        verdict = row['deep_research_verdict'] if row['deep_research_verdict'] else "-"
        rec = row['recommendation'] if row['recommendation'] else "-"

        if verdict == 'UNKNOWN' or verdict == 'ERROR_PARSING':
             verdict = "ERROR_PARSING"

        # Batch
        batch = "Compared" if (row['batch_id'] and row['batch_id'] > 0) else "-"
        if row['batch_winner']:
            batch = "🏆 WINNER"

        # Construct Row
        r = {
            "Date": date_str,
            "Symbol": symbol,
            "Market": row['region'],
            "Score": int(row['ai_score']) if pd.notna(row['ai_score']) else 0,
            "Rec": rec,
            "Price @ Dec": f"{price_dec:.2f}" if price_dec and pd.notna(price_dec) else "0.00",
            "Price +1W": price_1w_disp,
            "Perf 1W": perf_1w_disp,
            "Price +2W": price_2w_disp,
            "Perf 2W": perf_2w_disp,
            "Price +4W": price_4w_disp,
            "Perf 4W": perf_4w_disp,
            "SP500 1W": bench_res['SP500'],
            "Dow 1W": bench_res['Dow'],
            "DAX 1W": bench_res['DAX'],
            "Verdict": verdict,
            "Batch": batch,
            "Status": status
        }
        report_rows.append(r)
        
    # Create DataFrame
    report_df = pd.DataFrame(report_rows)
    
    # Sort
    report_df = report_df.sort_values(by="Date", ascending=False)
    
    # Display using tabulate or string format
    print(report_df.to_markdown(index=False))

if __name__ == "__main__":
    generate_report()
