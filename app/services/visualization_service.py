"""Console visualization mode for StockDrop.

Triggered by `python main.py --visualization`. Prints rich performance tables
and plotext line charts to the terminal, then the caller exits. Writes no files.

Output 1 (tables) reuses scripts/analysis/verdict_performance.py verbatim.
Output 2 (charts) plots equal-weight cumulative-basket returns vs an SPY
buy-and-hold reference, entering each position at its DB price_at_decision.
"""

from __future__ import annotations

import re
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

# yfinance on pandas 2.3+ emits a Timestamp.utcnow deprecation warning once per
# downloaded ticker — hundreds of lines of noise in this console report. Silence it.
warnings.filterwarnings("ignore", message=r".*Timestamp\.utcnow is deprecated.*")

from scripts.analysis.verdict_performance import (
    BENCHMARK,
    INTENT_LABEL,
    INTENT_ORDER,
    ROI_CLIP,
    build_table,
    fetch_prices,
    load_decisions,
    render_console,
)

WINDOWS: List[int] = [2, 4, 12]
MIN_N: int = 3  # match verdict_performance.py default


def parse_since(spec: str) -> datetime:
    """Parse a --since value into a cutoff datetime.

    Accepts a relative window — ``<n>w`` / ``<n>d`` / ``<n>m`` / ``<n>y`` (weeks,
    days, months, years ago; a space before the unit is allowed) — or an absolute
    date string such as ``2026-04-09``. Raises ValueError on anything unparseable.
    """
    s = spec.strip().lower()
    m = re.fullmatch(r"(\d+)\s*([wdmy])", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        now = datetime.now()
        if unit == "w":
            return now - timedelta(weeks=n)
        if unit == "d":
            return now - timedelta(days=n)
        if unit == "m":
            return (pd.Timestamp(now) - pd.DateOffset(months=n)).to_pydatetime()
        return (pd.Timestamp(now) - pd.DateOffset(years=n)).to_pydatetime()  # "y"
    try:
        return pd.to_datetime(spec).to_pydatetime()
    except Exception as exc:  # noqa: BLE001 — re-raise as a clear user-facing error
        raise ValueError(
            f"Could not parse --since value {spec!r}. "
            "Use a window like '4w', '30d', '3m', '1y', or a date like '2026-04-09'."
        ) from exc


def build_basket_curves(
    df: pd.DataFrame, prices: Dict[str, pd.Series], spy: pd.Series, intent_col: str
) -> dict:
    """Equal-weight cumulative-basket return per intent bucket over calendar time.

    At date t, a bucket's value = mean over positions already entered by t of
    clip(close_t / entry_price, 1±ROI_CLIP); plotted as (value-1)*100. entry_price
    is the row's price_at_decision; close_t is the yfinance close as-of t. The SPY
    reference is buy-and-hold normalized at the chart's start date.
    """
    axis = spy.index  # trading days we have benchmark prices for
    bucket_positions: Dict[str, list] = {}
    earliest = None

    for intent in INTENT_ORDER:
        sub = df[df[intent_col] == intent]
        positions = []
        for _, r in sub.iterrows():
            s = prices.get(r["symbol"])
            try:
                entry_price = float(r["price_at_decision"])
            except (TypeError, ValueError):
                entry_price = float("nan")
            if s is None or not (entry_price > 0):
                continue
            entry_ts = pd.Timestamp(r["date"]).normalize()
            positions.append((entry_ts, entry_price, s))
            if earliest is None or entry_ts < earliest:
                earliest = entry_ts
        if positions:
            bucket_positions[intent] = positions

    if not bucket_positions or earliest is None:
        return {"curves": {}, "spy_dates": [], "spy_vals": []}

    axis = axis[axis >= earliest]

    curves: Dict[str, dict] = {}
    for intent, positions in bucket_positions.items():
        cols = {}
        for i, (entry_ts, entry_price, s) in enumerate(positions):
            reindexed = s.reindex(axis).ffill()
            ratio = (reindexed / entry_price).clip(
                lower=1.0 - ROI_CLIP, upper=1.0 + ROI_CLIP
            )
            ratio[axis < entry_ts] = float("nan")  # not entered yet
            cols[i] = ratio
        mat = pd.DataFrame(cols, index=axis)
        counts = mat.count(axis=1)
        basket = mat.mean(axis=1)
        mask = counts > 0
        dates = list(axis[mask])
        if not dates:
            continue
        vals = list(((basket[mask] - 1.0) * 100.0).values)
        curves[intent] = {
            "dates": dates,
            "vals": vals,
            "final_n": int(counts[mask].iloc[-1]),
        }

    spy_axis = spy.reindex(axis).ffill()
    spy_start = float(spy_axis.iloc[0])
    spy_dates = list(axis)
    if not (spy_start > 0):
        spy_vals = [float("nan")] * len(spy_axis)
    else:
        spy_vals = list(((spy_axis / spy_start - 1.0) * 100.0).values)

    return {"curves": curves, "spy_dates": spy_dates, "spy_vals": spy_vals}


def render_basket_chart(title: str, payload: dict) -> None:
    """Draw cumulative-return lines for each bucket + an SPY reference, in-terminal.

    plotext has no true dashed style, so the SPY reference is distinguished by a
    distinct marker and an explicit '(buy & hold ref)' label.
    """
    import plotext as plt

    curves = payload.get("curves", {})
    if not curves:
        print(f"\n{title}: no data to chart.")
        return

    plt.clear_figure()
    plt.date_form("Y-m-d")
    plt.theme("pro")

    for intent in INTENT_ORDER:
        c = curves.get(intent)
        if not c:
            continue
        xs = [d.strftime("%Y-%m-%d") for d in c["dates"]]
        plt.plot(xs, c["vals"], label=f"{INTENT_LABEL[intent]} (n={c['final_n']})")

    if payload.get("spy_dates"):
        sxs = [d.strftime("%Y-%m-%d") for d in payload["spy_dates"]]
        plt.plot(sxs, payload["spy_vals"], label="SPY (buy & hold ref)", marker="dot")

    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel("Cumulative return %")
    plt.show()


def run_visualization(since: Optional[str] = None) -> None:
    """One-shot console report: alpha tables + cumulative-return charts. Writes no files.

    If ``since`` is given (a window like '4w'/'30d'/'3m'/'1y' or a date like
    '2026-04-09'), only decisions made on/after that cutoff are included.
    """
    df = load_decisions()

    if since:
        cutoff = parse_since(since)
        before = len(df)
        df = df[df["date"] >= pd.Timestamp(cutoff)].copy()
        print(f"Filtering to decisions since {cutoff.date()} — {len(df)} of {before}.")
        if df.empty:
            print("No decisions in that window — nothing to show.")
            return

    print(
        f"Loaded {len(df)} decisions "
        f"({df['date'].min().date()} -> {df['date'].max().date()})"
    )

    start = df["date"].min() - timedelta(days=5)
    end = datetime.now()
    prices = fetch_prices(df["symbol"].tolist(), start, end)
    spy = prices.get(BENCHMARK)
    if spy is None:
        print("Could not fetch SPY benchmark — aborting.")
        return
    print(f"Got prices for {len(prices) - 1} / {df['symbol'].nunique()} symbols.\n")

    # ---- OUTPUT 1: alpha-vs-SPY tables (reuse verdict_performance) ----
    council_tbl = build_table(df, prices, spy, WINDOWS, "council_intent")
    dr_tbl = build_table(df, prices, spy, WINDOWS, "dr_intent")
    render_console(
        "COUNCIL / PM verdict — alpha vs SPY (market-on-decision entry)",
        council_tbl, WINDOWS, MIN_N,
    )
    print()
    render_console(
        "DEEP RESEARCH verdict — alpha vs SPY (market-on-decision entry)",
        dr_tbl, WINDOWS, MIN_N,
    )
    print()

    # ---- OUTPUT 2: cumulative-return line charts ----
    pm_payload = build_basket_curves(df, prices, spy, "council_intent")
    render_basket_chart("Council / PM verdict — cumulative return vs SPY", pm_payload)

    dr_df = df[df["dr_intent"] != ""].copy()
    dr_payload = build_basket_curves(dr_df, prices, spy, "dr_intent")
    render_basket_chart("Deep Research verdict — cumulative return vs SPY", dr_payload)

    print("\nFootnotes:")
    print("  * Cumulative basket = equal-weight, market-on-decision entry at price_at_decision,")
    print("    buy-and-hold to today; each position's return clipped at +/-300%.")
    print("  * SPY line = buy & hold from each chart's start date.")
    print("  * The pre-Apr 9 2026 stretch comes from the legacy DB (data/subscribers.db),")
    print("    an earlier regime of the tool.")
    print("  * Console-only: no files were written.")
