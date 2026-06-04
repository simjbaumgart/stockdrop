"""Console visualization mode for StockDrop.

Triggered by `python main.py --visualization`. Prints rich performance tables
and plotext line charts to the terminal, then the caller exits. Writes no files.

Output 1 (tables) reuses scripts/analysis/verdict_performance.py verbatim.
Output 2 (charts) plots equal-weight cumulative-basket returns vs an SPY
buy-and-hold reference, entering each position at its DB price_at_decision.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd

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
