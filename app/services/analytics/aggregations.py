"""Aggregation primitives over enriched cohort DataFrames."""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

from app.services.analytics.intervals import (
    mean_ci,
    proportion_se,
    wilson_ci,
)

_AGG_COLS = [
    "count", "win_rate", "win_rate_se", "win_rate_ci_low", "win_rate_ci_high",
    "avg_return", "avg_return_se", "avg_return_ci_low", "avg_return_ci_high",
    "median_return", "std_return",
]


def _return_col(horizon: str) -> str:
    return f"return_{horizon}"


def _empty_winrate(group_col: str) -> pd.DataFrame:
    return pd.DataFrame(columns=[group_col, *_AGG_COLS])


def winrate_by(df: pd.DataFrame, group_col: str, horizon: str = "4w") -> pd.DataFrame:
    """Per-group descriptive stats for the realized horizon return.

    Output columns:
      count, win_rate, win_rate_se, win_rate_ci_low/high (Wilson 95%),
      avg_return, avg_return_se, avg_return_ci_low/high (t-based 95%),
      median_return, std_return.
    """
    col = _return_col(horizon)
    if df.empty or col not in df.columns or group_col not in df.columns:
        return _empty_winrate(group_col)

    sub = df.dropna(subset=[col]).copy()
    if sub.empty:
        return _empty_winrate(group_col)

    rows = []
    for grp, frame in sub.groupby(group_col, dropna=False):
        values = frame[col].astype(float).values
        n = int(len(values))
        wins = int((values > 0).sum())

        m_ci = mean_ci(values)
        wr_low, wr_high = wilson_ci(wins, n)
        row = {
            group_col: grp,
            "count": n,
            "win_rate": float(wins / n) if n else None,
            "win_rate_se": proportion_se(wins, n),
            "win_rate_ci_low": wr_low,
            "win_rate_ci_high": wr_high,
            "avg_return": m_ci["mean"],
            "avg_return_se": m_ci["se"],
            "avg_return_ci_low": m_ci["ci_low"],
            "avg_return_ci_high": m_ci["ci_high"],
            "median_return": float(np.median(values)) if n else None,
            "std_return": float(values.std(ddof=1)) if n > 1 else None,
        }
        rows.append(row)

    out = pd.DataFrame(rows)
    return out.sort_values("count", ascending=False).reset_index(drop=True)


def winrate_by_bucket(
    df: pd.DataFrame,
    value_col: str,
    bins: List[float],
    horizon: str = "4w",
    labels: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Bin a continuous column and compute winrate_by per bucket."""
    col = _return_col(horizon)
    if df.empty or col not in df.columns or value_col not in df.columns:
        return pd.DataFrame(columns=["bucket", "count", "win_rate", "avg_return"])

    sub = df.dropna(subset=[col, value_col]).copy()
    if sub.empty:
        return pd.DataFrame(columns=["bucket", "count", "win_rate", "avg_return"])
    sub["bucket"] = pd.cut(sub[value_col], bins=bins, labels=labels, include_lowest=True)
    return winrate_by(sub, group_col="bucket", horizon=horizon)


def equity_curve(df: pd.DataFrame, horizon: str = "4w") -> pd.DataFrame:
    """
    Equal-weight cumulative-return curve indexed by decision_date.
    Each decision contributes its horizon return; equity grows by daily mean contribution.
    """
    col = _return_col(horizon)
    if df.empty or col not in df.columns or "decision_date" not in df.columns:
        return pd.DataFrame(columns=["decision_date", "n", "avg_return", "equity"])

    sub = df.dropna(subset=[col]).copy().sort_values("decision_date")
    if sub.empty:
        return pd.DataFrame(columns=["decision_date", "n", "avg_return", "equity"])

    daily = (
        sub.groupby("decision_date")
        .agg(n=(col, "size"), avg_return=(col, "mean"))
        .reset_index()
    )
    daily["equity"] = (1.0 + daily["avg_return"]).cumprod()
    return daily


def time_to_recover_dist(df: pd.DataFrame, max_days: int = 40) -> pd.Series:
    """Histogram of days_to_recover for recovered decisions."""
    if "days_to_recover" not in df.columns:
        return pd.Series(dtype=int)
    sub = df.dropna(subset=["days_to_recover"])
    if sub.empty:
        return pd.Series(dtype=int)
    return sub["days_to_recover"].clip(upper=max_days).astype(int).value_counts().sort_index()
