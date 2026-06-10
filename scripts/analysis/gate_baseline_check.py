"""Gate baseline check — verify the numbers behind the decision-gate layer.

Re-runs the headline numbers from prompt_vs_outcome_analysis_2026-06-10 so the
post-gate deployment comparison is valid. Joins decision_points (subscribers.db,
repo root) to data/trade_report_full_7d.csv on (symbol, DATE(timestamp)) and
reports, for the analysis window:

  * PM buys (BUY + BUY_LIMIT): n, win rate, 10% trimmed mean, median 7d return
    (expected baseline: ~43% win, tmean ~-0.30%, median ~-0.68%)
  * The same split by drop_type
    (expected: SECTOR_ROTATION/MACRO_SELLOFF ~52% win, +1.74% tmean)
  * Buy-rate on EARNINGS_MISS drops vs everything else (expected: ~40% vs ~19%)
  * SA quant < 2.5 buys (expected: ~31% win, median ~-3.47%)

After the gates are live, it additionally compares gated-away decisions
(pre_gate_action was a buy, final action downgraded) against kept buys — the
free A/B the `pre_gate_action` column exists for.

Usage:
    python scripts/analysis/gate_baseline_check.py                # full window
    python scripts/analysis/gate_baseline_check.py --since 2026-04-09
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import pandas as pd
from scipy.stats import trim_mean

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.path.join(ROOT, "subscribers.db")
CSV_PATH = os.path.join(ROOT, "data", "trade_report_full_7d.csv")

BUY_ACTIONS = ("BUY", "BUY_LIMIT")
SECTOR_MACRO = ("SECTOR_ROTATION", "MACRO_SELLOFF")


def load_joined(since: str) -> pd.DataFrame:
    """decision_points joined to the 7d trade report on (symbol, decision date)."""
    conn = sqlite3.connect(DB_PATH)
    try:
        db = pd.read_sql_query(
            """
            SELECT id, symbol, DATE(timestamp) AS date, recommendation,
                   drop_type, conviction, sa_quant_rating, is_earnings_drop,
                   pre_gate_action, gates_fired
            FROM decision_points
            WHERE DATE(timestamp) >= ?
              AND symbol NOT IN ('MOCK_TEST', 'TEST', 'EXAMPLE')
            """,
            conn,
            params=(since,),
        )
    except Exception:
        # Pre-gate schema (before the decision_gate_service migration)
        db = pd.read_sql_query(
            """
            SELECT id, symbol, DATE(timestamp) AS date, recommendation,
                   drop_type, conviction, sa_quant_rating, is_earnings_drop
            FROM decision_points
            WHERE DATE(timestamp) >= ?
              AND symbol NOT IN ('MOCK_TEST', 'TEST', 'EXAMPLE')
            """,
            conn,
            params=(since,),
        )
        db["pre_gate_action"] = None
        db["gates_fired"] = None
    finally:
        conn.close()

    csv = pd.read_csv(CSV_PATH)
    csv = csv.rename(columns={"Date": "date", "Symbol": "symbol"})
    # Rows younger than the horizon carry a placeholder performance — drop them.
    csv = csv[~csv["Status"].astype(str).str.startswith("Pending")]
    csv["perf_7d"] = (
        csv["Performance"].astype(str).str.rstrip("%").replace("-", None).astype(float)
    )
    csv = csv.dropna(subset=["perf_7d"])[["date", "symbol", "perf_7d"]]
    csv = csv.drop_duplicates(subset=["date", "symbol"])

    return db.merge(csv, on=["date", "symbol"], how="left")


def describe(perf: pd.Series) -> str:
    perf = perf.dropna()
    if len(perf) == 0:
        return "n=0"
    win = (perf > 0).mean() * 100
    tmean = trim_mean(perf, 0.1) if len(perf) >= 3 else perf.mean()
    return (
        f"n={len(perf):3d}  win={win:4.0f}%  "
        f"tmean={tmean:+.2f}%  median={perf.median():+.2f}%"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default="2026-04-09", help="analysis window start (YYYY-MM-DD)")
    args = ap.parse_args()

    df = load_joined(args.since)
    rec = df["recommendation"].astype(str).str.upper()
    buys = df[rec.isin(BUY_ACTIONS)]
    buys_out = buys.dropna(subset=["perf_7d"])

    print(f"Window: {args.since} .. {df['date'].max()}   "
          f"decisions={len(df)}  buys={len(buys)}  buys w/ 7d outcome={len(buys_out)}")
    print()
    print("PM buys (BUY + BUY_LIMIT), 7d raw return")
    print(f"  all buys            {describe(buys_out['perf_7d'])}")
    print()
    print("By drop_type")
    sector_macro = buys_out[buys_out["drop_type"].isin(SECTOR_MACRO)]
    print(f"  SECTOR/MACRO        {describe(sector_macro['perf_7d'])}")
    for dt in ("EARNINGS_MISS", "COMPANY_SPECIFIC", "ANALYST_DOWNGRADE",
               "TECHNICAL_BREAKDOWN", "UNKNOWN"):
        sub = buys_out[buys_out["drop_type"] == dt]
        if len(sub):
            print(f"  {dt:<19} {describe(sub['perf_7d'])}")
    print()

    # The source analysis split on the screener's is_earnings_drop flag,
    # not the PM-assigned drop_type (which under-counts earnings drops).
    decided = df[rec.isin(("BUY", "BUY_LIMIT", "WATCH", "AVOID"))]
    earn = decided[decided["is_earnings_drop"] == 1]
    other = decided[decided["is_earnings_drop"] != 1]
    earn_rate = earn["recommendation"].str.upper().isin(BUY_ACTIONS).mean() * 100 if len(earn) else float("nan")
    other_rate = other["recommendation"].str.upper().isin(BUY_ACTIONS).mean() * 100 if len(other) else float("nan")
    print(f"Buy-rate: earnings drops {earn_rate:.0f}% (n={len(earn)})  "
          f"vs otherwise {other_rate:.0f}% (n={len(other)})")
    print()

    low_quant = buys_out[buys_out["sa_quant_rating"].notna() & (buys_out["sa_quant_rating"] < 2.5)]
    covered = df["sa_quant_rating"].notna().mean() * 100
    print(f"SA quant < 2.5 buys   {describe(low_quant['perf_7d'])}   (coverage {covered:.0f}% of decisions)")
    print()

    # --- post-deployment A/B: gated-away vs kept buys --------------------
    gated = df[df["gates_fired"].notna() & (df["gates_fired"].astype(str) != "")
               & df["gates_fired"].astype(str).str.upper().ne("NONE")]
    if len(gated):
        gated_away = gated[gated["pre_gate_action"].astype(str).str.upper().isin(BUY_ACTIONS)
                           & ~gated["recommendation"].astype(str).str.upper().isin(BUY_ACTIONS)]
        print("Gate A/B (live since gates deployed)")
        print(f"  gated-away buys     {describe(gated_away['perf_7d'])}")
        print(f"  kept buys           {describe(buys_out['perf_7d'])}")
        print("  success criterion: gated-away underperform kept; buy win rate >= 50%")
    else:
        print("Gate A/B: no gated decisions yet (pre_gate_action/gates_fired empty).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
