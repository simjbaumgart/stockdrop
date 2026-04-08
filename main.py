from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import os
import logging

load_dotenv()

# Increase file descriptor limit for macOS to prevent [Errno 24]
try:
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    # Use 65536 or hard limit, whichever is smaller, to avoid OS hard caps
    target_limit = min(65536, hard) if hard > 0 else 65536
    resource.setrlimit(resource.RLIMIT_NOFILE, (target_limit, hard))
except Exception as e:
    print(f"Warning: Could not set file descriptor limit: {e}")

# Suppress noisy third-party loggers in production
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)

from app.routers import views, api, subscriptions
import asyncio
from datetime import datetime
from app.services.stock_service import stock_service
from app.services.storage_service import storage_service
from app.services.email_service import email_service
from app.database import init_db, DB_NAME

from app.services.performance_service import performance_service

import subprocess

def get_git_version():
    try:
        return subprocess.check_output(["git", "describe", "--tags", "--always"], stderr=subprocess.STDOUT).decode("utf-8").strip()
    except Exception as e:
        print(f"Error fetching git version: {e}")
        return "unknown"

app = FastAPI(title="StockDrop")
VERSION = get_git_version()

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(views.router)
from app.routers import performance
app.include_router(performance.router)
app.include_router(api.router, prefix="/api")
app.include_router(subscriptions.router, prefix="/api")

@app.get("/health")
def health_check():
    return {"status": "ok", "version": VERSION}

def _print_recent_decisions(label="Decisions"):
    """Print recent decision points from DB — today + last scanned day."""
    import sqlite3
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Find today's decisions
        today_str = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT symbol, recommendation, conviction, risk_reward_ratio,
                   drop_percent, deep_research_verdict, timestamp
            FROM decision_points
            WHERE date(timestamp) = ?
            ORDER BY timestamp DESC
        """, (today_str,))
        today_rows = cursor.fetchall()

        # Find last scanned day (most recent day before today with decisions)
        cursor.execute("""
            SELECT DISTINCT date(timestamp) as scan_date
            FROM decision_points
            WHERE date(timestamp) < ?
            ORDER BY scan_date DESC
            LIMIT 1
        """, (today_str,))
        last_day_row = cursor.fetchone()
        last_day_rows = []
        last_scan_date = None

        if last_day_row:
            last_scan_date = last_day_row['scan_date']
            cursor.execute("""
                SELECT symbol, recommendation, conviction, risk_reward_ratio,
                       drop_percent, deep_research_verdict, timestamp
                FROM decision_points
                WHERE date(timestamp) = ?
                ORDER BY timestamp DESC
            """, (last_scan_date,))
            last_day_rows = cursor.fetchall()

        # Print header
        def _print_table(rows, header):
            if not rows:
                return
            print(f"\n  [{header}] ({len(rows)} decisions)")
            print(f"  {'Symbol':<8} {'Rec':<12} {'Conv':<10} {'R/R':<6} {'Drop%':<8} {'DR Verdict':<12} {'Time'}")
            print(f"  {'-'*8} {'-'*12} {'-'*10} {'-'*6} {'-'*8} {'-'*12} {'-'*19}")
            for r in rows:
                symbol = r['symbol'] or ''
                rec = r['recommendation'] or ''
                conv = r['conviction'] or '-'
                rr = f"{r['risk_reward_ratio']:.1f}" if r['risk_reward_ratio'] else '-'
                drop = f"{r['drop_percent']:.1f}%" if r['drop_percent'] else '-'
                verdict = r['deep_research_verdict'] or '-'
                ts = r['timestamp'] or ''
                print(f"  {symbol:<8} {rec:<12} {conv:<10} {rr:<6} {drop:<8} {verdict:<12} {ts}")

        print(f"\n[{label}] {datetime.now().strftime('%H:%M:%S')}")

        if today_rows:
            _print_table(today_rows, f"Today ({today_str})")
        else:
            print(f"  No decisions today ({today_str}).")

        if last_day_rows:
            _print_table(last_day_rows, f"Last scan ({last_scan_date})")
        elif not today_rows:
            # No decisions anywhere recent — show overall stats
            cursor.execute("SELECT MAX(timestamp) as last_ts, COUNT(*) as total FROM decision_points")
            info = cursor.fetchone()
            last_ts = info['last_ts'] if info else 'never'
            total = info['total'] if info else 0
            print(f"  Last activity: {last_ts} ({total} total in DB)")

        conn.close()
    except Exception as e:
        print(f"[{label}] Could not read decisions: {e}")

@app.on_event("startup")
async def startup_event():
    print(f"\n{'='*50}")
    print(f"  StockDrop v{VERSION}")
    print(f"  Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")
    init_db()
    _print_recent_decisions(label="Startup")
    asyncio.create_task(run_periodic_check())
    asyncio.create_task(run_decision_log())
    asyncio.create_task(run_storage_upload())
    asyncio.create_task(run_daily_summary())
    asyncio.create_task(run_performance_tracking())
    asyncio.create_task(run_trade_report_update())

async def run_periodic_check():
    check_interval = 1200 # 20 minutes
    log_interval = 300   # 5 minutes
    last_check_time = 0  # Ensure first run happens immediately

    while True:
        try:
            now_ts = datetime.now().timestamp()
            if now_ts - last_check_time >= check_interval:
                print(f"[Scheduler] Running Periodic Stock Drop Check... {datetime.now().strftime('%H:%M:%S')}")
                # Run the check in a thread pool to avoid blocking the event loop
                # since check_large_cap_drops is synchronous (uses yfinance)
                await asyncio.to_thread(stock_service.check_large_cap_drops)
                last_check_time = datetime.now().timestamp()
            else:
                next_check = datetime.fromtimestamp(last_check_time + check_interval)
                time_remaining = next_check - datetime.now()
                minutes_remaining = int(time_remaining.total_seconds() / 60)
                # Handle potential negative remaining if we slightly overshot or logic drift, default to 0
                minutes_remaining = max(0, minutes_remaining)
                # Heartbeat: show index status between scans so console stays active
                try:
                    indices = await asyncio.to_thread(stock_service.get_indices)
                    sp500 = indices.get("S&P 500", {})
                    stoxx = indices.get("STOXX 600", {})
                    sp_price = sp500.get("price", "N/A")
                    sp_chg = sp500.get("change_percent", 0)
                    stoxx_price = stoxx.get("price", "N/A")
                    stoxx_chg = stoxx.get("change_percent", 0)
                    print(f"[Heartbeat] {datetime.now().strftime('%H:%M:%S')} | S&P 500: {sp_price} ({sp_chg:+.2f}%) | STOXX 600: {stoxx_price} ({stoxx_chg:+.2f}%) | Next scan in {minutes_remaining}m")
                except Exception:
                    print(f"[Heartbeat] {datetime.now().strftime('%H:%M:%S')} | Next scan in {minutes_remaining}m")
        except Exception as e:
            print(f"Error in periodic check: {e}")
        
        await asyncio.sleep(log_interval)

async def run_decision_log():
    """Print DB decisions table every 10 minutes for operator visibility."""
    await asyncio.sleep(600)  # First print after 10 minutes (startup already printed)
    while True:
        try:
            _print_recent_decisions(label="Decisions")
        except Exception as e:
            print(f"[Decisions] Error: {e}")
        await asyncio.sleep(600)  # Every 10 minutes

async def run_trade_report_update():
    """
    Updates the trade_report_full.csv every 5 minutes.
    """
    from scripts.core import generate_trade_report
    while True:
        try:
            print(f"[Scheduler] Updating Trade Report CSV... {datetime.now().strftime('%H:%M:%S')}")
            # Run in thread pool to avoid blocking
            await asyncio.to_thread(generate_trade_report.main)
            print("[Scheduler] Trade Report Update Completed.")
        except Exception as e:
            print(f"[Scheduler] Error updating trade report: {e}")
        await asyncio.sleep(3600) # Run every 60 minutes

async def run_storage_upload():
    while True:
        try:
            print("Running Storage upload...")
            # Fetch current data
            indices_data = stock_service.get_indices()
            # Upload to GCS
            await asyncio.to_thread(storage_service.upload_data, indices_data)
            # Save locally
            await asyncio.to_thread(storage_service.save_locally, indices_data)
        except Exception as e:
            print(f"Error in Storage upload: {e}")
        await asyncio.sleep(43200) # Run every 12 hours

async def run_daily_summary():
    """
    Checks once an hour if it's time to send the daily summary (e.g., after 22:00 local time).
    """
    last_sent_date = None
    
    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            
            # Send summary after 22:00 (10 PM) if not sent today
            if now.hour >= 22 and last_sent_date != today_str:
                print("Generating Daily Summary...")
                movers = await asyncio.to_thread(stock_service.get_daily_movers, 5.0)
                await asyncio.to_thread(email_service.send_daily_summary, movers)
                last_sent_date = today_str
                print("Daily Summary completed.")
                
        except Exception as e:
            print(f"Error in daily summary task: {e}")
            
        await asyncio.sleep(3600) # Check every hour

async def run_performance_tracking():
    """
    Records performance metrics once a day (e.g., after market close).
    """
    last_run_date = None
    
    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            
            # Run after 23:00 (11 PM) to ensure markets are closed
            if now.hour >= 23 and last_run_date != today_str:
                print("Running Daily Performance Tracking...")
                count = await asyncio.to_thread(performance_service.record_daily_performance)
                print(f"Daily Performance Tracking completed. Recorded {count} snapshots.")
                last_run_date = today_str
                
        except Exception as e:
            print(f"Error in performance tracking task: {e}")
            
        await asyncio.sleep(3600) # Check every hour

if __name__ == "__main__":
    import uvicorn
    import sys
    
    if "--enable-email" in sys.argv:
        email_service.enabled = True
        print("Email notifications ENABLED.")
    else:
        print("Email notifications disabled (default).")
        
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
