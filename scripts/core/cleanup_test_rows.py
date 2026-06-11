"""One-off: delete test-fixture rows that leaked into the production DB.

v0.8.2-288 review finding #1: GATETEST/CLEANTEST/DRTEST (2026-06-10) and
TEST/TEST_T* (2026-05-09) rows sit in decision_points and pollute the trade
report. SELECT-first, prints what it deletes, idempotent.

Usage:
    python scripts/core/cleanup_test_rows.py            # dry-run (default)
    python scripts/core/cleanup_test_rows.py --execute  # actually delete
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.path.join(ROOT, "subscribers.db")

# LIKE '%TEST%' per the review, but list real-ticker exceptions explicitly
# if any ever exist (none today — no NYSE/Nasdaq ticker contains 'TEST').
SELECT_SQL = "SELECT id, symbol, DATE(timestamp), recommendation FROM decision_points WHERE symbol LIKE '%TEST%'"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="delete instead of dry-run")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    rows = cur.execute(SELECT_SQL).fetchall()
    if not rows:
        print("No test rows found — nothing to do.")
        return 0
    print(f"{'DELETING' if args.execute else 'WOULD DELETE'} {len(rows)} rows:")
    for r in rows:
        print(f"  id={r[0]:>5}  {r[1]:<12} {r[2]}  {r[3]}")
    ids = [r[0] for r in rows]
    if args.execute:
        ph = ",".join("?" * len(ids))
        # children first (FK decision_id), then parents
        for table in ("decision_tracking", "agent_token_usage"):
            try:
                cur.execute(f"DELETE FROM {table} WHERE decision_id IN ({ph})", ids)
                print(f"  deleted {cur.rowcount} child rows from {table}")
            except sqlite3.OperationalError:
                pass  # table may not exist in this DB
        cur.execute(f"DELETE FROM decision_points WHERE id IN ({ph})", ids)
        conn.commit()
        remaining = cur.execute(SELECT_SQL).fetchall()
        print(f"Done. Remaining test rows: {len(remaining)}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
