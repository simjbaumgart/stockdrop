"""Verdict performance vs SP500 — terminal readout.

Measures how StockDrop's verdicts performed against the S&P 500 (SPY) over
2 / 4 / 12-week holding windows, split by:

  * Deep Research (DR) verdict bucket
  * Council / PM (`recommendation`) verdict bucket
  * DR-vs-Council disagreement (did the DR override add or destroy alpha?)

Design choices (locked with the user):
  * Entry = market-on-decision  -> buy at `price_at_decision` on the decision date.
  * Primary number = ALPHA vs SPY = (stock return over [D, D+W]) - (SPY return over [D, D+W]).
  * Returns reconstructed retroactively from yfinance (decision_tracking is empty/unreliable).
  * Both DBs merged: live `subscribers.db` (Apr 2026+) + legacy `data/subscribers.db`
    (Dec 2025 - Mar 2026). The 12-week cohort comes almost entirely from the legacy DB,
    which ran an earlier regime of the tool — flagged in the output.

Usage:
    python scripts/analysis/verdict_performance.py                 # all windows, console + charts
    python scripts/analysis/verdict_performance.py --windows 2,4   # subset of windows
    python scripts/analysis/verdict_performance.py --no-charts     # skip PNGs
    python scripts/analysis/verdict_performance.py --min-n 5       # hide cohorts smaller than N

Outputs PNGs to docs/images/verdict_performance_*.png
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import warnings
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DBS = [os.path.join(ROOT, "subscribers.db"), os.path.join(ROOT, "data", "subscribers.db")]
BENCHMARK = "SPY"
ROI_CLIP = 3.0  # cap |return| at 300% to kill corporate-action / split artifacts
IMG_DIR = os.path.join(ROOT, "docs", "images")

# Intent buckets we report on. Council uses `recommendation`, DR uses `deep_research_verdict`.
# Both are normalised through the same map so labels stay consistent across DB regimes.
INTENT_ORDER = ["ENTER_NOW", "ENTER_LIMIT", "AVOID", "NEUTRAL"]
INTENT_LABEL = {
    "ENTER_NOW": "BUY (enter now)",
    "ENTER_LIMIT": "BUY_LIMIT (enter on limit)",
    "AVOID": "AVOID / WAIT",
    "NEUTRAL": "WATCH / HOLD",
}


def normalize_to_intent(recommendation: str) -> str:
    """Map legacy + v0.9 nomenclature to a canonical intent.

    Mirrors app/services/performance_service.normalize_to_intent so this script
    stays consistent with the live pipeline. Kept local to avoid importing the
    full app (and its heavy deps) just for one function.
    """
    # NaN (a float, from a NULL DB cell) or None -> no verdict. NaN != NaN is the
    # only value that is unequal to itself, so this catches it without importing math.
    if recommendation is None or recommendation != recommendation:
        return ""
    rec = str(recommendation).strip().upper().replace("*", "").strip()
    if not rec or rec in ("NONE", "0.0", "UNKNOWN (PARSE ERROR)", "ANALYZING", "PENDING"):
        return ""
    if rec in ("STRONG BUY", "STRONG_BUY", "BUY"):
        return "ENTER_NOW"
    if rec in ("SPECULATIVE BUY", "SPECULATIVE_BUY", "BUY_LIMIT"):
        return "ENTER_LIMIT"
    if rec in ("AVOID", "SELL", "SHORT SELL", "STRONG SELL", "STRONG_SELL",
               "HARD_AVOID", "WAIT_FOR_STABILIZATION", "PASS", "PASS_INSUFFICIENT_DATA"):
        return "AVOID"
    if rec in ("HOLD", "WATCH"):
        return "NEUTRAL"
    if "AVOID" in rec or "SELL" in rec:
        return "AVOID"
    if "BUY" in rec:
        return "ENTER_LIMIT"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_decisions() -> pd.DataFrame:
    """Load and merge decision_points from all available DBs."""
    frames = []
    for db in DBS:
        if not os.path.exists(db):
            continue
        conn = sqlite3.connect(db)
        try:
            df = pd.read_sql_query(
                """
                SELECT symbol, price_at_decision, recommendation,
                       deep_research_verdict, timestamp, drop_type,
                       is_earnings_drop
                FROM decision_points
                WHERE price_at_decision > 0
                  AND symbol NOT IN ('MOCK_TEST', 'TEST', 'EXAMPLE')
                """,
                conn,
            )
        except Exception as e:  # legacy DB may lack a column
            df = pd.read_sql_query(
                """
                SELECT symbol, price_at_decision, recommendation,
                       deep_research_verdict, timestamp
                FROM decision_points
                WHERE price_at_decision > 0
                  AND symbol NOT IN ('MOCK_TEST', 'TEST', 'EXAMPLE')
                """,
                conn,
            )
            df["drop_type"] = None
            df["is_earnings_drop"] = None
        finally:
            conn.close()
        df["__db"] = os.path.basename(db)
        frames.append(df)

    if not frames:
        sys.exit("No databases found. Looked for: " + ", ".join(DBS))

    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["timestamp"], errors="coerce").dt.tz_localize(None)
    df = df.dropna(subset=["date"])

    # De-duplicate: same symbol + same calendar day across DBs -> keep the live one.
    df["__day"] = df["date"].dt.date
    df = df.sort_values("__db")  # 'data/...' sorts after 'subscribers.db' -> keep=first keeps live
    df = df.drop_duplicates(subset=["symbol", "__day"], keep="first")

    df["council_intent"] = df["recommendation"].apply(normalize_to_intent)
    df["dr_intent"] = df["deep_research_verdict"].apply(normalize_to_intent)
    return df


# ---------------------------------------------------------------------------
# Price reconstruction
# ---------------------------------------------------------------------------

def fetch_prices(symbols, start, end) -> dict:
    """Batch-download daily adjusted closes. Returns {symbol: pd.Series indexed by date}."""
    symbols = sorted(set(symbols) | {BENCHMARK})
    print(f"Fetching prices for {len(symbols)} symbols ({start.date()} -> {end.date()})...")
    data = yf.download(
        symbols, start=start.strftime("%Y-%m-%d"),
        end=(end + timedelta(days=2)).strftime("%Y-%m-%d"),
        progress=False, threads=True, group_by="ticker", auto_adjust=True,
    )
    out = {}
    for sym in symbols:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if sym not in data.columns.get_level_values(0):
                    continue
                s = data[sym]["Close"].dropna()
            else:
                s = data["Close"].dropna()
            if len(s):
                s.index = pd.to_datetime(s.index).tz_localize(None)
                out[sym] = s
        except Exception:
            continue
    return out


def price_on_or_after(series: pd.Series, target: datetime):
    """First available close on or after `target` (handles weekends/holidays)."""
    if series is None or series.empty:
        return None
    sub = series[series.index >= pd.Timestamp(target).normalize()]
    return float(sub.iloc[0]) if len(sub) else None


def window_alpha(prices, spy, symbol, decision_date, weeks):
    """Return (raw_ret, alpha) for holding `symbol` over `weeks` from decision_date.

    None if the window hasn't matured yet or prices are missing.
    """
    exit_date = decision_date + timedelta(weeks=weeks)
    if exit_date > datetime.now():
        return None  # window not matured
    s = prices.get(symbol)
    entry = price_on_or_after(s, decision_date)
    exit_ = price_on_or_after(s, exit_date)
    spy_entry = price_on_or_after(spy, decision_date)
    spy_exit = price_on_or_after(spy, exit_date)
    if not all([entry, exit_, spy_entry, spy_exit]):
        return None
    raw = exit_ / entry - 1.0
    bench = spy_exit / spy_entry - 1.0
    raw = max(min(raw, ROI_CLIP), -ROI_CLIP)
    return raw, raw - bench


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def cohort_stats(values):
    """values = list of (raw, alpha). Returns dict of summary stats."""
    if not values:
        return None
    raws = [v[0] for v in values]
    alphas = [v[1] for v in values]
    n = len(values)
    wins = sum(1 for a in alphas if a > 0)
    return {
        "n": n,
        "raw_mean": sum(raws) / n,
        "alpha_mean": sum(alphas) / n,
        "alpha_median": sorted(alphas)[n // 2],
        "win_rate": wins / n,
    }


def build_table(df, prices, spy, windows, intent_col):
    """Return {intent: {week: stats}} for the given intent column."""
    table = {}
    for intent in INTENT_ORDER:
        sub = df[df[intent_col] == intent]
        row = {}
        for w in windows:
            vals = []
            for _, r in sub.iterrows():
                res = window_alpha(prices, spy, r["symbol"], r["date"], w)
                if res:
                    vals.append(res)
            row[w] = cohort_stats(vals)
        table[intent] = row
    return table


def build_override_stats(df, prices, spy, windows):
    """Compare DR vs Council where they DISAGREE.

    Three groups:
      * DR upgraded  (council AVOID/NEUTRAL -> DR ENTER*)
      * DR downgraded (council ENTER* -> DR AVOID/NEUTRAL)
      * They agreed on a BUY
    Measured on what the DR said to do (entry only happens if DR is a buy;
    for downgrades we measure the avoided position = what you DIDN'T buy).
    """
    def is_buy(x):
        return x in ("ENTER_NOW", "ENTER_LIMIT")

    has_dr = df[(df["dr_intent"] != "")].copy()
    groups = {
        "DR upgraded to BUY": has_dr[(~has_dr["council_intent"].apply(is_buy)) & (has_dr["dr_intent"].apply(is_buy))],
        "DR downgraded from BUY": has_dr[(has_dr["council_intent"].apply(is_buy)) & (~has_dr["dr_intent"].apply(is_buy))],
        "Both agreed BUY": has_dr[(has_dr["council_intent"].apply(is_buy)) & (has_dr["dr_intent"].apply(is_buy))],
    }
    out = {}
    for name, sub in groups.items():
        row = {}
        for w in windows:
            vals = []
            for _, r in sub.iterrows():
                res = window_alpha(prices, spy, r["symbol"], r["date"], w)
                if res:
                    vals.append(res)
            row[w] = cohort_stats(vals)
        out[name] = row
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_console(title, table, windows, min_n):
    from rich.console import Console
    from rich.table import Table

    console = Console()
    t = Table(title=title, title_style="bold cyan", header_style="bold", show_lines=True)
    t.add_column("Verdict bucket", style="white", no_wrap=True)
    for w in windows:
        t.add_column(f"{w}w\nalpha / med\nn · win%", justify="right")

    for intent, row in table.items():
        label = INTENT_LABEL.get(intent, intent)
        cells = [label]
        any_data = False
        for w in windows:
            s = row.get(w)
            if not s or s["n"] < min_n:
                cells.append("[dim]–[/dim]")
                continue
            any_data = True
            alpha = s["alpha_mean"]
            color = "green" if alpha > 0 else "red"
            cells.append(
                f"[{color}]{alpha*100:+.1f}%[/{color}] / {s['alpha_median']*100:+.1f}%\n"
                f"[dim]n={s['n']} · {s['win_rate']*100:.0f}%[/dim]"
            )
        if any_data:
            t.add_row(*cells)
    console.print(t)


def render_charts(council_tbl, dr_tbl, windows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(IMG_DIR, exist_ok=True)
    paths = []

    for name, tbl in [("council", council_tbl), ("dr", dr_tbl)]:
        fig, ax = plt.subplots(figsize=(10, 6))
        intents = [i for i in INTENT_ORDER if any(tbl[i].get(w) for w in windows)]
        x = range(len(intents))
        width = 0.8 / max(len(windows), 1)
        for j, w in enumerate(windows):
            vals = [(tbl[i].get(w)["alpha_mean"] * 100 if tbl[i].get(w) else 0) for i in intents]
            ns = [(tbl[i].get(w)["n"] if tbl[i].get(w) else 0) for i in intents]
            offs = [xi + j * width for xi in x]
            bars = ax.bar(offs, vals, width, label=f"{w}w")
            for b, nval in zip(bars, ns):
                if nval:
                    ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
                            f"n={nval}", ha="center",
                            va="bottom" if b.get_height() >= 0 else "top", fontsize=7)
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xticks([xi + width * (len(windows) - 1) / 2 for xi in x])
        ax.set_xticklabels([INTENT_LABEL[i] for i in intents], rotation=15, ha="right")
        ax.set_ylabel("Mean alpha vs SPY (%)")
        ax.set_title(f"{'Deep Research' if name=='dr' else 'Council / PM'} verdict — alpha vs SPY by holding window")
        ax.legend(title="Window")
        fig.tight_layout()
        p = os.path.join(IMG_DIR, f"verdict_performance_{name}.png")
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--windows", default="2,4,12", help="comma-separated weeks, e.g. 2,4,12")
    ap.add_argument("--min-n", type=int, default=3, help="hide cohorts smaller than this")
    ap.add_argument("--no-charts", action="store_true", help="skip PNG generation")
    args = ap.parse_args()

    windows = [int(w) for w in args.windows.split(",") if w.strip()]

    df = load_decisions()
    print(f"Loaded {len(df)} decisions ({df['date'].min().date()} -> {df['date'].max().date()})")

    start = df["date"].min() - timedelta(days=5)
    end = datetime.now()
    prices = fetch_prices(df["symbol"].tolist(), start, end)
    spy = prices.get(BENCHMARK)
    if spy is None:
        sys.exit("Could not fetch SPY benchmark — aborting.")
    print(f"Got prices for {len(prices)-1} / {df['symbol'].nunique()} symbols.\n")

    council_tbl = build_table(df, prices, spy, windows, "council_intent")
    dr_tbl = build_table(df, prices, spy, windows, "dr_intent")
    override_tbl = build_override_stats(df, prices, spy, windows)

    render_console("COUNCIL / PM verdict — alpha vs SPY (market-on-decision entry)", council_tbl, windows, args.min_n)
    print()
    render_console("DEEP RESEARCH verdict — alpha vs SPY (market-on-decision entry)", dr_tbl, windows, args.min_n)
    print()
    render_console("DR vs COUNCIL — does the override add alpha?", override_tbl, windows, 1)

    print("\nNotes:")
    print("  * alpha = stock return minus SPY over the identical holding window (market-on-decision entry).")
    print("  * 12w cohort is dominated by the legacy DB (Dec 2025-Mar 2026) — an earlier tool regime.")
    print("  * Returns clipped at +/-300% to suppress corporate-action artifacts.")
    print("  * 'DR downgraded from BUY' alpha = the position you AVOIDED; positive alpha there means")
    print("    the DR correctly steered you out of a winner (bad), negative means it dodged a loser (good).")

    if not args.no_charts:
        paths = render_charts(council_tbl, dr_tbl, windows)
        print("\nCharts written:")
        for p in paths:
            print("  " + p)


if __name__ == "__main__":
    main()
