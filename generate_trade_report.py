import os
import sys
import time
import sqlite3
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from dateutil import parser
import pytz

DB_PATH = os.getenv("DB_PATH", "subscribers.db")

def get_decision_points():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM decision_points ORDER BY timestamp DESC")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error fetching decision points: {e}")
        return []

def main():
    print("Generating Trade Decision Report (Optimized)...")
    decisions = get_decision_points()
    
    if not decisions:
        print("No trade decisions found.")
        return

    print(f"Found {len(decisions)} decisions. Identifying symbols...")
    
    unique_symbols = list(set([d['symbol'] for d in decisions if d.get('symbol')]))
    
    # Add Benchmarks
    benchmarks = ['^GSPC', '^DJI', '^GDAXI']
    all_symbols = unique_symbols + benchmarks
    
    # Cache Configuration
    CACHE_FILE = os.path.join("data", "yfinance_cache.pkl")
    CACHE_DURATION = 6 * 3600  # 6 hours

    all_history = pd.DataFrame()
    cache_valid = False

    if os.path.exists(CACHE_FILE):
        # Check file age
        file_age = time.time() - os.path.getmtime(CACHE_FILE)
        if file_age < CACHE_DURATION:
            print(f"Loading data from cache ({file_age/3600:.1f}h old)...")
            try:
                all_history = pd.read_pickle(CACHE_FILE)
                if not all_history.empty:
                    cache_valid = True
            except Exception as e:
                print(f"Error loading cache: {e}")
    
    if not cache_valid:
        # 1. Batch Download History (1 Year should cover recent decisions)
        print(f"Cache miss or expired. Batch fetching data for {len(all_symbols)} symbols (including benchmarks)...")
        
        # Chunking symbols to avoid URL length issues or API limits
        chunk_size = 100
        
        for i in range(0, len(all_symbols), chunk_size):
            chunk = all_symbols[i:i+chunk_size]
            print(f"  Downloading chunk {i}-{i+len(chunk)}...")
            try:
                 # Using threads=True for speed
                data = yf.download(chunk, period="1y", progress=False, threads=True, group_by='ticker')
                if not data.empty:
                    if len(chunk) == 1:
                         # If single ticker, yfinance returns dataframe with columns 'Open', 'Close' etc.
                         # We need to make it MultiIndex to be consistent if possible, or just handle it.
                         # Easiest is to add ticker as top level level if it's missing
                         if isinstance(data.columns, pd.Index) and not isinstance(data.columns, pd.MultiIndex):
                             # Create MultiIndex
                             iterables = [[chunk[0]], data.columns]
                             data.columns = pd.MultiIndex.from_product(iterables, names=['Ticker', 'Price'])
                    
                    if all_history.empty:
                        all_history = data
                    else:
                        all_history = pd.concat([all_history, data], axis=1)
            except Exception as e:
                print(f"Error downloading chunk: {e}")

        # Save to cache
        if not all_history.empty:
             try:
                 all_history.to_pickle(CACHE_FILE)
                 print("Data cached successfully.")
             except Exception as e:
                 print(f"Error caching data: {e}")

    report_data = []
    
    # Helper to clean/access data
    def get_price_from_history(ticker, date_obj):
        try:
            if ticker not in all_history.columns.levels[0]:
                return None
            ts_data = all_history[ticker]
            
            # ts_data should have 'Close'
            if 'Close' not in ts_data:
                return None
                
            ts_date_str = date_obj.strftime('%Y-%m-%d')
            
            # Direct lookup string
            try:
                val = ts_data['Close'].loc[ts_date_str]
                return float(val)
            except KeyError:
                # Try next 3 days
                for i in range(1, 4):
                    next_d = (date_obj + timedelta(days=i)).strftime('%Y-%m-%d')
                    try:
                         val = ts_data['Close'].loc[next_d]
                         return float(val)
                    except KeyError:
                        pass
                return None
        except Exception:
            return None

    # Helper for Current Price (Latest available in DF)
    def get_latest_price(ticker):
        try:
            if ticker not in all_history.columns.levels[0]:
                return None
            ts_data = all_history[ticker]
            
            # Get last valid index
            last_valid = ts_data['Close'].last_valid_index()
            if last_valid:
                return float(ts_data['Close'].loc[last_valid])
            return None
        except:
            return None

    print("Processing decisions...")
    
    for d in decisions:
        symbol = d.get('symbol')
        timestamp_str = d.get('timestamp')
        
        try:
            decision_dt = parser.parse(timestamp_str)
        except:
            continue
            
        price_at_decision = d.get('price_at_decision')
        recommendation = d.get('recommendation')
        score = d.get('ai_score')
        region = d.get('region') # Get the region/market
        
        week_later_dt = decision_dt + timedelta(days=7)
        now = datetime.now()
        is_future = week_later_dt > now
        
        # Get Prices from Batch Data
        if not price_at_decision:
             price_at_decision = get_price_from_history(symbol, decision_dt)
        
        week_later_price = None
        current_status_price = get_latest_price(symbol)
        
        if is_future:
            week_later_price = current_status_price
            status = "Pending (<1w)"
        else:
            week_later_price = get_price_from_history(symbol, week_later_dt)
            if week_later_price is None:
                week_later_price = current_status_price
            status = "Completed"

        # Calculate Drop/Gain
        perf_pct = 0.0
        if price_at_decision and week_later_price:
            perf_pct = ((week_later_price - price_at_decision) / price_at_decision) * 100
            
        # Benchmark Calculations
        bench_data = {}
        for bench_ticker, bench_name in [('^GSPC', 'SP500'), ('^DJI', 'Dow'), ('^GDAXI', 'DAX')]:
            bench_start = get_price_from_history(bench_ticker, decision_dt)
            
            # Determine end date for benchmark
            if is_future:
                 bench_end = get_latest_price(bench_ticker)
            else:
                 bench_end = get_price_from_history(bench_ticker, week_later_dt)
                 # Fallback if no data on exact week later date (e.g. holiday), try latest
                 if bench_end is None:
                      bench_end = get_latest_price(bench_ticker) 

            if bench_start and bench_end:
                bench_perf = ((bench_end - bench_start) / bench_start) * 100
                bench_data[f"{bench_name} 1W"] = f"{bench_perf:+.2f}%"
            else:
                bench_data[f"{bench_name} 1W"] = "-"

        deep_research_verdict = d.get('deep_research_verdict')
        batch_id = d.get('batch_id')
        batch_winner = d.get('batch_winner')
        
        batch_status = "-"
        if batch_winner:
            batch_status = "🏆 WINNER"
        elif batch_id:
            batch_status = "Compared"

        row = {
            "Date": decision_dt.strftime("%Y-%m-%d"),
            "Symbol": symbol,
            "Market": region if region else "Unknown",
            "Score": f"{int(score)}" if score is not None else "-",
            "Rec": recommendation,
            "Price @ Dec": f"{price_at_decision:.2f}" if price_at_decision else "-",
            "Price +1W": f"{week_later_price:.2f}" if week_later_price else "-",
            "Performance": f"{perf_pct:+.2f}%" if price_at_decision and week_later_price else "-",
            "Verdict": deep_research_verdict if deep_research_verdict else "-",
            "Batch": batch_status,
            "Status": status
        }
        # Add benchmark data
        row.update(bench_data)
        
        report_data.append(row)

    report_data.sort(key=lambda x: x['Date'], reverse=True)
    
    # Save Full CSV
    csv_file = os.path.join("data", "trade_report_full.csv")
    pd.DataFrame(report_data).to_csv(csv_file, index=False)
    print(f"Full CSV report saved to {csv_file}")
    
    # Generate Truncated Markdown Table (Top 100)
    LIMIT = 100
    shown_data = report_data[:LIMIT]
    
    headers = ["Date", "Symbol", "Market", "Score", "Rec", "Price @ Dec", "Price +1W", "Performance", "SP500 1W", "Dow 1W", "DAX 1W", "Verdict", "Batch", "Status"]
    widths = {h: len(h) for h in headers}
    for row in shown_data:
        for h in headers:
            val = str(row.get(h, '-'))
            widths[h] = max(widths[h], len(val))
            
    fmt = "| " + " | ".join([f"{{:<{widths[h]}}}" for h in headers]) + " |"
    
    print(f"\n# Trade Decision Report (Latest {LIMIT})")
    print(f"\n> [!TIP]\n> This table shows the latest {LIMIT} decisions. For the full history, please see the [CSV Report](file://{os.getcwd()}/{csv_file}).\n")
    print(fmt.format(*headers))
    print("| " + " | ".join(["-" * widths[h] for h in headers]) + " |")
    
    count = 0
    for row in shown_data:
        print(fmt.format(*[str(row.get(h, '-')) for h in headers]))
        count += 1
        
    print(f"\nTotal Decisions: {len(report_data)} (Showing {count})")

if __name__ == "__main__":
    main()
