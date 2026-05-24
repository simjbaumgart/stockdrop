"""Aggregate decisions + positions into the monthly_summary CSV and headline stats."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict

import pandas as pd


def _format_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:+.2f}%"


def _format_count(value: float | int) -> str:
    if pd.isna(value):
        return "0"
    return str(int(value))


def build_monthly_summary(decisions: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """One row per recommendation: counts + outcome metrics from desk_positions.

    Joins on decisions.id == positions.decision_point_id. Open positions
    contribute to counts but not to win_rate / mean_realized_pnl_pct.
    """
    if decisions.empty:
        return pd.DataFrame(
            columns=[
                "recommendation", "count", "mean_drop_pct", "mean_ai_score",
                "n_with_positions", "n_closed", "win_rate", "mean_realized_pnl_pct",
            ]
        )

    # decisions -> verdict counts and means
    base = (
        decisions.groupby("recommendation")
        .agg(
            count=("id", "count"),
            mean_drop_pct=("drop_percent", "mean"),
            mean_ai_score=("ai_score", "mean"),
        )
        .reset_index()
    )

    # join positions on decision_point_id -> id.
    # suffixes=("", "_dec") keeps positions.id as "id" (and renames
    # decisions.id to "id_dec") so the agg below can reference "id"
    # without a column-collision KeyError.
    joined = positions.merge(
        decisions[["id", "recommendation"]],
        left_on="decision_point_id",
        right_on="id",
        how="inner",
        suffixes=("", "_dec"),
    )
    closed = joined[joined["status"] == "CLOSED"]
    pos_agg = (
        joined.groupby("recommendation")
        .agg(n_with_positions=("id", "count"))
        .reset_index()
    )
    closed_agg = (
        closed.groupby("recommendation")
        .agg(
            n_closed=("id", "count"),
            mean_realized_pnl_pct=("realized_pnl_pct", "mean"),
            win_rate=("realized_pnl_pct", lambda s: (s > 0).mean()),
        )
        .reset_index()
    )

    out = base.merge(pos_agg, on="recommendation", how="left").merge(
        closed_agg, on="recommendation", how="left"
    )
    out["n_with_positions"] = out["n_with_positions"].fillna(0).astype(int)
    out["n_closed"] = out["n_closed"].fillna(0).astype(int)
    return out


def compute_headline_stats(
    decisions: pd.DataFrame,
    positions: pd.DataFrame,
    as_of: str,
    since_days: int,
) -> Dict[str, str]:
    """Return template-ready stat strings (already formatted, never None)."""
    end = datetime.strptime(as_of, "%Y-%m-%d")
    start = end - timedelta(days=since_days)

    closed = positions[positions["status"] == "CLOSED"] if not positions.empty else positions
    open_pos = positions[positions["status"] == "ACTIVE"] if not positions.empty else positions

    counts = decisions["recommendation"].value_counts() if not decisions.empty else pd.Series(dtype=int)
    win_rate = (closed["realized_pnl_pct"] > 0).mean() if not closed.empty else None
    mean_pnl = closed["realized_pnl_pct"].mean() if not closed.empty else None

    return {
        "as_of": as_of,
        "window_start": start.strftime("%Y-%m-%d"),
        "window_end": end.strftime("%Y-%m-%d"),
        "total_decisions": _format_count(len(decisions)),
        "n_buy": _format_count(counts.get("BUY", 0)),
        "n_buy_limit": _format_count(counts.get("BUY_LIMIT", 0)),
        "n_watch": _format_count(counts.get("WATCH", 0)),
        "n_avoid": _format_count(counts.get("AVOID", 0)),
        "n_positions_total": _format_count(len(positions)),
        "n_positions_closed": _format_count(len(closed)),
        "n_positions_open": _format_count(len(open_pos)),
        "overall_win_rate": "—" if win_rate is None else f"{win_rate * 100:.1f}%",
        "mean_realized_pnl_pct": _format_pct(mean_pnl),
    }
