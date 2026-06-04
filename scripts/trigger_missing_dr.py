#!/usr/bin/env python3
"""
trigger_missing_dr.py — same-day Deep Research rescue.

Checks the buy-side decisions (BUY / BUY_LIMIT) recorded for a given date and
reports which ones have a Deep Research verdict and which are still missing one.
Unless --dry-run is passed, it queues Deep Research for the missing rows and
drains the DR worker to completion (printing progress), then re-prints the
status table.

This is the manual counterpart to the per-cycle backfill in
StockService._process_deep_research_backfill. Both share the same selection
policy (MISSING_DR_WHERE) and context builder (build_backfill_dr_context), so a
row rescued here is built identically to the live pipeline.

Why this exists: the live pipeline routes EVERY BUY/BUY_LIMIT through DR, but a
timed run (`main.py --run-for N`) can shut down before the DR queue drains,
stranding rows as "Pending DR Review" with no verdict. This rescues them
without waiting for the next full scan cycle.

Usage:
    python3 scripts/trigger_missing_dr.py                 # today, trigger missing
    python3 scripts/trigger_missing_dr.py --dry-run       # today, report only
    python3 scripts/trigger_missing_dr.py --date 2026-06-04
    python3 scripts/trigger_missing_dr.py --limit 3       # cap how many to queue
    python3 scripts/trigger_missing_dr.py --timeout 1800  # max seconds to wait
"""
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime

import pytz

# Ensure repo root is importable when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.stock_service import MISSING_DR_WHERE, build_backfill_dr_context
from app.services.deep_research_service import deep_research_service


def _today_str() -> str:
    """Eastern-time date — matches how decision rows are stamped."""
    return datetime.now(pytz.timezone("US/Eastern")).strftime("%Y-%m-%d")


def _fetch_buyside_rows(date_str: str) -> list:
    """All BUY/BUY_LIMIT decisions for the date, with their DR status."""
    conn = sqlite3.connect(os.getenv("DB_PATH", "subscribers.db"))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, symbol, recommendation, conviction, risk_reward_ratio,
               deep_research_verdict, deep_research_review_verdict, status
        FROM decision_points
        WHERE date(timestamp) = ?
          AND recommendation IN ('BUY', 'BUY_LIMIT')
        ORDER BY symbol
        """,
        (date_str,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def _fetch_missing(date_str: str) -> list:
    """Buy-side rows still missing a DR verdict (shared policy with backfill)."""
    conn = sqlite3.connect(os.getenv("DB_PATH", "subscribers.db"))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT * FROM decision_points WHERE {MISSING_DR_WHERE}",
        (date_str,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def _has_verdict(v) -> bool:
    return bool(v) and v not in ("-", "ERROR_PARSING", "PENDING_REVIEW") and not str(v).startswith("UNKNOWN")


def _print_status(date_str: str) -> list:
    """Print the buy-side DR status table; return the rows still missing DR."""
    rows = _fetch_buyside_rows(date_str)
    print(f"\n=== Buy-side decisions for {date_str} ({len(rows)} total) ===")
    if not rows:
        print("  (none)")
        return []
    print(f"  {'SYM':7} {'REC':10} {'CONV':9} {'R/R':>5}  {'DR?':4} {'VERDICT':14} STATUS")
    missing = []
    for r in rows:
        done = _has_verdict(r["deep_research_verdict"])
        if not done:
            missing.append(r)
        rr = r["risk_reward_ratio"]
        rr_s = f"{rr:.2f}" if isinstance(rr, (int, float)) else str(rr)
        print(
            f"  {r['symbol']:7} {r['recommendation']:10} {str(r['conviction']):9} "
            f"{rr_s:>5}  {'✓' if done else '✗':4} "
            f"{str(r['deep_research_verdict'] or '-'):14} {r['status']}"
        )
    print(f"\n  {len(rows) - len(missing)} with DR · {len(missing)} missing")
    return missing


def _drain(timeout_s: int, poll_s: int = 15) -> None:
    """Block until the DR worker queue empties and no task is active."""
    print(f"\n[Drain] Waiting for Deep Research worker (timeout {timeout_s}s)...")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        active = deep_research_service.active_tasks_count
        q_ind = deep_research_service.individual_queue.qsize()
        q_batch = deep_research_service.batch_queue.qsize()
        if active == 0 and q_ind == 0 and q_batch == 0:
            print("[Drain] Queue empty and no active task. Done.")
            return
        mins = int((deadline - time.monotonic())) // 60
        print(f"  [Drain] active={active} queued={q_ind} (batch={q_batch}) · ~{mins}m budget left")
        time.sleep(poll_s)
    print("[Drain] Timeout reached — some tasks may still be running in the background.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Same-day Deep Research rescue for buy-side decisions.")
    parser.add_argument("--date", default=None, help="Date YYYY-MM-DD (default: today, US/Eastern)")
    parser.add_argument("--dry-run", action="store_true", help="Report status only; do not queue DR")
    parser.add_argument("--limit", type=int, default=None, help="Max number of missing rows to queue")
    parser.add_argument("--timeout", type=int, default=2400, help="Max seconds to wait for the worker to drain")
    args = parser.parse_args()

    date_str = args.date or _today_str()

    missing = _print_status(date_str)

    if not missing:
        print("\nNothing to do — every buy-side decision already has a Deep Research verdict.")
        return 0

    # Re-fetch full rows (status table only selected a few columns) for context build.
    full_missing = _fetch_missing(date_str)
    if args.limit is not None:
        full_missing = full_missing[: args.limit]

    if args.dry_run:
        print(f"\n[Dry run] {len(full_missing)} row(s) would be queued: {[c['symbol'] for c in full_missing]}")
        return 0

    if not deep_research_service.is_running:
        print("\n[Error] Deep Research service is not running (missing GEMINI_API_KEY?). Cannot trigger.")
        return 1

    print(f"\n[Trigger] Queuing {len(full_missing)} missing row(s): {[c['symbol'] for c in full_missing]}")
    queued = 0
    for c in full_missing:
        context = build_backfill_dr_context(c, date_str)
        print(f"  > Triggering {c['symbol']} (Conviction: {c.get('conviction')}, R/R: {c.get('risk_reward_ratio')})...")
        if deep_research_service.queue_research_task(symbol=c["symbol"], context=context, decision_id=c["id"]):
            queued += 1
            print(f"  > Queued {c['symbol']}")
        else:
            print(f"  > Skipped {c['symbol']}: already in-flight")

    if queued == 0:
        print("\nNothing queued (all already in-flight).")
        return 0

    _drain(args.timeout)

    # Final status after draining.
    _print_status(date_str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
