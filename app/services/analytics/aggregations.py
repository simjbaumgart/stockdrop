"""Aggregation primitives over enriched cohort DataFrames."""
from __future__ import annotations

from typing import List, Optional

import pandas as pd


def _return_col(horizon: str) -> str:
    return f"return_{horizon}"


def winrate_by(df: pd.DataFrame, group_col: str, horizon: str = "4w") -> pd.DataFrame:
    """count / win_rate / avg_return / median_return / std_return per group."""
    col = _return_col(horizon)
    if df.empty or col not in df.columns or group_col not in df.columns:
        return pd.DataFrame(
            columns=[group_col, "count", "win_rate", "avg_return", "median_return", "std_return"]
        )

    sub = df.dropna(subset=[col]).copy()
    if sub.empty:
        return pd.DataFrame(
            columns=[group_col, "count", "win_rate", "avg_return", "median_return", "std_return"]
        )
    sub["_win"] = (sub[col] > 0).astype(int)

    grouped = (
        sub.groupby(group_col, dropna=False)
        .agg(
            count=(col, "size"),
            win_rate=("_win", "mean"),
            avg_return=(col, "mean"),
            median_return=(col, "median"),
            std_return=(col, "std"),
        )
        .reset_index()
    )
    return grouped.sort_values("count", ascending=False).reset_index(drop=True)


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
