"""Does a risk/reward (RR) ratio predict realized outcome?

Splits each verdict bucket by the stated RR and measures the realized forward
outcome (raw return and alpha vs SPY, market-on-decision entry) over 2 / 4 / 12-
week holding windows. Works for either source:

  * ``--source pm`` (default): Council/PM ``recommendation`` + ``risk_reward_ratio``
  * ``--source dr``:           ``deep_research_verdict`` + ``deep_research_rr_ratio``

Reuses the existing pipeline:
  * normalize_to_intent / window_alpha  — from scripts/analysis/verdict_performance.py
  * fetch_prices_cached                 — from app/services/visualization_service.py
    (so the slow Yahoo download is served from the same-day disk cache)

Usage:
    python scripts/analysis/rr_outcome.py            # PM
    python scripts/analysis/rr_outcome.py --source dr
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import timedelta

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from scripts.analysis.verdict_performance import (  # noqa: E402
    BENCHMARK,
    INTENT_LABEL,
    INTENT_ORDER,
    normalize_to_intent,
    window_alpha,
)
from app.services.visualization_service import fetch_prices_cached  # noqa: E402

DBS = [os.path.join(ROOT, "subscribers.db"), os.path.join(ROOT, "data", "subscribers.db")]
WINDOWS = [2, 4, 12]
# RR bands: poor / thin / decent / strong reward-to-risk, per the PM's own number.
RR_BANDS = [(0.0, 0.5, "RR<0.5"), (0.5, 1.0, "0.5-1.0"),
            (1.0, 2.0, "1.0-2.0"), (2.0, float("inf"), "RR>=2.0")]


# Source -> (verdict column, RR column, human label)
SOURCES = {
    "pm": ("recommendation", "risk_reward_ratio", "Council / PM"),
    "dr": ("deep_research_verdict", "deep_research_rr_ratio", "Deep Research"),
}


def load(verdict_col: str, rr_col: str) -> pd.DataFrame:
    frames = []
    for db in DBS:
        if not os.path.exists(db):
            continue
        conn = sqlite3.connect(db)
        df = pd.read_sql_query(
            f"""SELECT symbol, {verdict_col} AS verdict, {rr_col} AS rr_raw,
                       price_at_decision, timestamp
                FROM decision_points
                WHERE price_at_decision > 0
                  AND symbol NOT IN ('MOCK_TEST','TEST','EXAMPLE')""",
            conn,
        )
        df["__db"] = os.path.basename(db)
        frames.append(df)
        conn.close()
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["timestamp"], errors="coerce").dt.tz_localize(None)
    df = df.dropna(subset=["date"])
    # De-dup symbol+day across DBs, keep live (subscribers.db sorts before data/...).
    df["__day"] = df["date"].dt.date
    df = df.sort_values("__db").drop_duplicates(subset=["symbol", "__day"], keep="first")
    df["intent"] = df["verdict"].apply(normalize_to_intent)
    df["rr"] = pd.to_numeric(df["rr_raw"], errors="coerce")
    return df


def stats(rows, prices, spy, weeks):
    raws, alphas = [], []
    for _, r in rows.iterrows():
        res = window_alpha(prices, spy, r["symbol"], r["date"], weeks)
        if res:
            raws.append(res[0])
            alphas.append(res[1])
    if not alphas:
        return None
    n = len(alphas)
    return {
        "n": n,
        "ret": sum(raws) / n * 100,
        "alpha": sum(alphas) / n * 100,
        "alpha_med": sorted(alphas)[n // 2] * 100,
        "win": sum(1 for a in alphas if a > 0) / n * 100,
    }


def fmt(s):
    if not s:
        return f"{'—':^26}"
    cell = f"α{s['alpha']:+5.1f}/{s['alpha_med']:+5.1f} n{s['n']:<3} {s['win']:3.0f}%w"
    return f"{cell:<26}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=list(SOURCES), default="pm",
                    help="pm = Council/PM verdict+RR (default); dr = Deep Research verdict+RR")
    args = ap.parse_args()
    verdict_col, rr_col, label = SOURCES[args.source]

    df = load(verdict_col, rr_col)
    print(f"=== {label} verdict — RR vs realized outcome "
          f"({verdict_col} / {rr_col}) ===")
    print(f"Loaded {len(df)} decisions ({df['date'].min().date()} -> {df['date'].max().date()})")
    rr = df["rr"]
    print(f"RR populated: {rr.notna().sum()}/{len(df)} ({rr.notna().mean()*100:.0f}%)\n")

    print(f"RR coverage + median by {label} verdict bucket:")
    for intent in INTENT_ORDER:
        sub = df[df["intent"] == intent]
        if not len(sub):
            continue
        cov = sub["rr"].notna().mean() * 100
        med = sub["rr"].median()
        print(f"  {INTENT_LABEL[intent]:<28} n={len(sub):<4} RR-coverage {cov:3.0f}%  "
              f"median RR {med if pd.notna(med) else float('nan'):.2f}")
    print()

    end = pd.Timestamp.now()
    start = (df["date"].min() - timedelta(days=5)).to_pydatetime()
    prices = fetch_prices_cached(df["symbol"].tolist(), start, end)
    spy = prices.get(BENCHMARK)
    if spy is None:
        sys.exit("No SPY benchmark.")
    print()

    have_rr = df[df["rr"].notna()].copy()

    for intent in INTENT_ORDER:
        sub = have_rr[have_rr["intent"] == intent]
        if len(sub) < 10:
            continue
        print("=" * 96)
        print(f"{INTENT_LABEL[intent]}  —  outcome split by {label} risk/reward "
              f"(decisions with an RR: n={len(sub)})")
        print(f"{'RR band':<9}{'n':>4}   " + "".join(f"{str(w)+'w':<26}" for w in WINDOWS))
        print(f"{'':9}{'':>4}   " + "".join(f"{'α(mean/med) n win%':<26}" for w in WINDOWS))
        for lo, hi, lbl in RR_BANDS:
            band = sub[(sub["rr"] >= lo) & (sub["rr"] < hi)]
            if not len(band):
                continue
            cells = [f"{lbl:<9}{len(band):>4}   "]
            for w in WINDOWS:
                cells.append(fmt(stats(band, prices, spy, w)))
            print("".join(cells))
        # Spearman correlation RR vs realized 4w alpha within this bucket.
        pairs = []
        for _, r in sub.iterrows():
            res = window_alpha(prices, spy, r["symbol"], r["date"], 4)
            if res:
                pairs.append((r["rr"], res[1]))
        if len(pairs) >= 8:
            pr = pd.DataFrame(pairs, columns=["rr", "alpha4w"])
            rho = pr["rr"].corr(pr["alpha4w"], method="spearman")
            print(f"  Spearman(RR, 4w alpha) = {rho:+.3f}  (n={len(pairs)})  "
                  f"[+ = higher PM RR → better realized alpha]")
        print()

    print("Notes: alpha = stock return minus SPY over the same window (market-on-decision entry,")
    print("returns clipped at +/-300%). Only decisions whose window has matured are counted, so")
    print(f"12w n is much smaller than 2w. RR is the {label} {rr_col} at decision time.")


if __name__ == "__main__":
    main()
