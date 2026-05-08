from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import os
import signal
import logging

load_dotenv()

# Survive BrokenPipeError from print() when the terminal/SSH/tmux reader
# goes away mid-write. Python's default already keeps the process alive on
# SIGPIPE (writes fail with BrokenPipeError rather than terminating), but
# an unhandled BrokenPipeError in a background task can still kill that
# task. This wrapper catches it and detaches stdout on first hit so
# subsequent writes become silent no-ops and the service keeps running.
import sys as _sys
import builtins as _builtins

def _safe_print_wrapper(original_print):
    def _print(*args, **kwargs):
        try:
            return original_print(*args, **kwargs)
        except BrokenPipeError:
            try:
                _sys.stdout = open(os.devnull, "w")
            except Exception:
                pass
    return _print

_builtins.print = _safe_print_wrapper(_builtins.print)

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
from app.database import init_db

from app.services.performance_service import performance_service
from app.services.deep_research_service import deep_research_service
from app.utils.agent_call_counter import counter as agent_call_counter

import subprocess

# Graceful shutdown support
shutdown_event = asyncio.Event()
# Read from env var so the value survives uvicorn's re-import of this module.
# (python main.py loads as __main__; uvicorn re-imports as "main" — a separate module)
run_for_minutes = int(os.environ["STOCKDROP_RUN_FOR"]) if os.environ.get("STOCKDROP_RUN_FOR") else None

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

async def run_shutdown_timer(minutes: int):
    """Run for the specified duration, then gracefully shut down."""
    print(f"[StockDrop] Shutdown timer set: will stop in {minutes} minutes.")
    await asyncio.sleep(minutes * 60)
    print(f"\n[StockDrop] Timer expired ({minutes}m). Initiating graceful shutdown...")
    shutdown_event.set()

    # Stop deep research from picking up new tasks
    deep_research_service.is_running = False

    # Wait for in-flight deep research to finish (up to 15 min)
    print("[StockDrop] Waiting for in-flight deep research to finish...")
    completed = await asyncio.to_thread(deep_research_service.wait_for_completion, timeout_minutes=15)
    if completed:
        print("[StockDrop] All deep research tasks finished.")
    else:
        print("[StockDrop] Timed out waiting for deep research. Shutting down anyway.")

    print("[StockDrop] Graceful shutdown complete. Stopping server.")
    os.kill(os.getpid(), signal.SIGTERM)

@app.on_event("startup")
async def startup_event_handler():
    print(f"\n{'='*50}")
    print(f"  StockDrop {VERSION}")
    if run_for_minutes:
        print(f"  Mode: Timed run ({run_for_minutes} minutes)")
    print(f"  Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")
    init_db()
    asyncio.create_task(run_periodic_check())
    asyncio.create_task(run_storage_upload())
    asyncio.create_task(run_daily_summary())
    asyncio.create_task(run_performance_tracking())
    asyncio.create_task(run_trade_report_update())
    if run_for_minutes:
        asyncio.create_task(run_shutdown_timer(run_for_minutes))

async def _interruptible_sleep(seconds: float) -> bool:
    """Sleep for up to `seconds`, returning True if shutdown was requested."""
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=seconds)
        return True  # shutdown requested
    except asyncio.TimeoutError:
        return False  # normal timeout

async def run_periodic_check():
    check_interval = 1200 # 20 minutes
    log_interval = 300   # 5 minutes
    last_check_time = 0  # Ensure first run happens immediately

    while not shutdown_event.is_set():
        try:
            now_ts = datetime.now().timestamp()
            if now_ts - last_check_time >= check_interval:
                if shutdown_event.is_set():
                    break
                print(f"[Scheduler] Running Periodic Stock Drop Check... {datetime.now().strftime('%H:%M:%S')}")
                try:
                    await asyncio.to_thread(stock_service.check_large_cap_drops)
                finally:
                    last_check_time = datetime.now().timestamp()
                    # Per-cycle agent-call quota telemetry
                    snap = agent_call_counter.snapshot()
                    print(
                        f"[agent-quota] cycle_total={snap['total_cycle']} "
                        f"rolling_24h={snap['total_rolling_24h']} "
                        f"by_agent={snap['by_agent']}"
                    )
                    agent_call_counter.reset_cycle()
            else:
                next_check = datetime.fromtimestamp(last_check_time + check_interval)
                time_remaining = next_check - datetime.now()
                minutes_remaining = int(time_remaining.total_seconds() / 60)
                minutes_remaining = max(0, minutes_remaining)
                print(f"[Scheduler] Next Stock Drop Check in {minutes_remaining} minutes... ({next_check.strftime('%H:%M:%S')})")
        except Exception as e:
            print(f"Error in periodic check: {e}")

        if await _interruptible_sleep(log_interval):
            break
    print("[Scheduler] Stock scanner stopped.")

async def run_trade_report_update():
    """
    Updates the trade_report_full.csv every 60 minutes.
    """
    from scripts.core import generate_trade_report
    while not shutdown_event.is_set():
        try:
            print(f"[Scheduler] Updating Trade Report CSV... {datetime.now().strftime('%H:%M:%S')}")
            await asyncio.to_thread(generate_trade_report.main)
            print("[Scheduler] Trade Report Update Completed.")
        except Exception as e:
            print(f"[Scheduler] Error updating trade report: {e}")
        if await _interruptible_sleep(3600):
            break

async def run_storage_upload():
    while not shutdown_event.is_set():
        try:
            print("Running Storage upload...")
            indices_data = stock_service.get_indices()
            await asyncio.to_thread(storage_service.upload_data, indices_data)
            await asyncio.to_thread(storage_service.save_locally, indices_data)
        except Exception as e:
            print(f"Error in Storage upload: {e}")
        if await _interruptible_sleep(43200):
            break

async def run_daily_summary():
    """
    Checks once an hour if it's time to send the daily summary (e.g., after 22:00 local time).
    """
    last_sent_date = None

    while not shutdown_event.is_set():
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            if now.hour >= 22 and last_sent_date != today_str:
                print("Generating Daily Summary...")
                movers = await asyncio.to_thread(stock_service.get_daily_movers, 5.0)
                await asyncio.to_thread(email_service.send_daily_summary, movers)
                last_sent_date = today_str
                print("Daily Summary completed.")

        except Exception as e:
            print(f"Error in daily summary task: {e}")

        if await _interruptible_sleep(3600):
            break

async def run_performance_tracking():
    """
    Records performance metrics once a day (e.g., after market close).
    """
    last_run_date = None

    while not shutdown_event.is_set():
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            if now.hour >= 23 and last_run_date != today_str:
                print("Running Daily Performance Tracking...")
                count = await asyncio.to_thread(performance_service.record_daily_performance)
                print(f"Daily Performance Tracking completed. Recorded {count} snapshots.")
                last_run_date = today_str

        except Exception as e:
            print(f"Error in performance tracking task: {e}")

        if await _interruptible_sleep(3600):
            break

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="StockDrop — automated stock dip-buying tool")
    parser.add_argument("--enable-email", action="store_true", help="Enable email notifications")
    parser.add_argument("--run-for", type=int, default=None, metavar="MINUTES",
                        help="Run for N minutes then gracefully shut down (default: run forever)")
    args = parser.parse_args()

    if args.enable_email:
        email_service.enabled = True
        print("Email notifications ENABLED.")
    else:
        print("Email notifications disabled (default).")

    if args.run_for:
        # Propagate via env var so uvicorn's re-import of this module picks it up.
        os.environ["STOCKDROP_RUN_FOR"] = str(args.run_for)

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=not args.run_for)
