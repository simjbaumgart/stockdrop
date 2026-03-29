import sys
import os
import argparse
import sqlite3
import warnings

# Add the project root to the python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# Suppress yfinance warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from app.services.performance_service import normalize_to_intent
from app.database import get_tracking_history

DB_PATH = os.getenv("DB_PATH", "subscribers.db")


def _evaluate_with_yfinance(decisions, limit=None):
    """Fast path: batch-fetch prices via yfinance instead of per-symbol TradingView."""
    import yfinance as yf
    import pandas as pd
    from dateutil import parser

    unique_symbols = list(set(d.get("symbol") for d in decisions if d.get("symbol") and d.get("symbol") not in ("MOCK_TEST", "TEST", "EXAMPLE")))
    if not unique_symbols:
        return []

    # Batch download latest prices
    data = yf.download(unique_symbols, period="5d", progress=False, threads=True, group_by="ticker", auto_adjust=True)
    price_cache = {}
    if not data.empty:
        for sym in unique_symbols:
            try:
                if len(unique_symbols) == 1:
                    col = data["Close"] if "Close" in data.columns else data.iloc[:, 0]
                else:
                    tickers = data.columns.get_level_values(0).unique() if isinstance(data.columns, pd.MultiIndex) else []
                    if sym in list(tickers):
                        sub = data[sym] if isinstance(data.columns, pd.MultiIndex) else data
                        col = sub["Close"] if "Close" in sub.columns else sub.iloc[:, 0]
                    else:
                        col = None
                if col is not None:
                    valid = col.dropna()
                    if len(valid) > 0:
                        price_cache[sym] = float(valid.iloc[-1])
            except Exception:
                pass

    results = []
    for d in decisions:
        symbol = d.get("symbol")
        if symbol in ("MOCK_TEST", "TEST", "EXAMPLE"):
            continue
        start_price = d.get("price_at_decision")
        recommendation = d.get("recommendation")
        intent = normalize_to_intent(recommendation)
        current_price = price_cache.get(symbol, 0.0)
        if not current_price:
            performance_percent = 0.0
        else:
            performance_percent = ((current_price - start_price) / start_price) * 100

        outcome = "NEUTRAL"
        if intent in ("ENTER_NOW", "ENTER_LIMIT"):
            outcome = "PROFIT" if performance_percent > 2.0 else ("LOSS" if performance_percent < -2.0 else "NEUTRAL")
        elif intent == "AVOID":
            outcome = "AVOIDED" if "SELL" not in (recommendation or "").upper() else ("SAVED" if performance_percent < -2.0 else ("MISSED" if performance_percent > 2.0 else "NEUTRAL"))

        results.append({
            "id": d.get("id"),
            "symbol": symbol,
            "timestamp": d.get("timestamp"),
            "recommendation": recommendation,
            "intent": intent,
            "start_price": start_price,
            "current_price": current_price,
            "performance_percent": performance_percent,
            "outcome": outcome,
            "reasoning": d.get("reasoning"),
        })
    return results


def main():
    parser = argparse.ArgumentParser(description="Evaluate stock trading decisions (performance vs current price)")
    parser.add_argument("--limit", "-n", type=int, default=None, help="Limit to N most recent decisions")
    parser.add_argument("--yfinance", "-y", action="store_true", help="Use yfinance for prices (faster, no TradingView)")
    args = parser.parse_args()

    print("Fetching decisions and calculating performance...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if args.limit:
        cursor.execute("SELECT * FROM decision_points ORDER BY timestamp DESC LIMIT ?", (args.limit,))
        print(f"(Limited to {args.limit} most recent decisions)")
    else:
        cursor.execute("SELECT * FROM decision_points ORDER BY timestamp DESC")
    decisions = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if args.yfinance:
        print("(Using yfinance for current prices)\n")
        results = _evaluate_with_yfinance(decisions, args.limit)
    else:
        if args.limit:
            import app.database as db
            _orig = db.get_decision_points
            db.get_decision_points = lambda: decisions
            try:
                from app.services.performance_service import performance_service
                results = performance_service.evaluate_decisions()
            finally:
                db.get_decision_points = _orig
        else:
            from app.services.performance_service import performance_service
            results = performance_service.evaluate_decisions()
    print()
    
    if not results:
        print("No decisions found.")
        return

    # Filter out SKIP recommendations
    results = [r for r in results if r.get('recommendation') != 'SKIP']
    
    if not results:
        print("No decisions found (after filtering SKIP).")
        return

    # Header (Rec column widened for STRONG BUY, BUY_LIMIT, etc.)
    header = f"{'Symbol':<8} | {'Date':<20} | {'Rec':<12} | {'Intent':<12} | {'Start $':<10} | {'Curr $':<10} | {'Perf %':<10} | {'Outcome':<8} | {'Tracked':<8}"
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    
    for r in results:
        symbol = r['symbol']
        date = str(r['timestamp'])[:19]
        rec = (r.get('recommendation') or "-")[:12]
        intent = r.get('intent', "-")
        start = f"${r['start_price']:.2f}"
        curr = f"${r['current_price']:.2f}"
        perf = f"{r['performance_percent']:+.2f}%"
        outcome = r['outcome']
        
        # Get tracking count
        history = get_tracking_history(r['id'])
        tracked_count = len(history)
        
        # Color coding (using ANSI escape codes)
        RESET = "\033[0m"
        RED = "\033[91m"
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        DIM = "\033[2m"
        
        color = RESET
        if outcome in ["PROFIT", "SAVED"]:
            color = GREEN
        elif outcome in ["LOSS", "MISSED"]:
            color = RED
        elif outcome == "NEUTRAL":
            color = YELLOW
        elif outcome == "AVOIDED":
            color = DIM
            
        print(f"{color}{symbol:<8} | {date:<20} | {rec:<12} | {intent:<12} | {start:<10} | {curr:<10} | {perf:<10} | {outcome:<8} | {tracked_count:<8}{RESET}")

    print("-" * len(header))

if __name__ == "__main__":
    main()
