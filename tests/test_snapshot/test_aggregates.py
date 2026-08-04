"""Tests for monthly summary aggregation and headline stats."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.services.snapshot.aggregates import (
    build_monthly_summary,
    compute_headline_stats,
)
from app.services.snapshot.db_export import load_decisions, load_positions


def test_monthly_summary_has_one_row_per_verdict(snapshot_db: Path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    summary = build_monthly_summary(decisions, positions)
    # Fixture has 4 distinct recommendations in-window: BUY, BUY_LIMIT, WATCH, AVOID
    assert set(summary["recommendation"]) == {"BUY", "BUY_LIMIT", "WATCH", "AVOID"}


def test_monthly_summary_win_rate_and_pnl(snapshot_db: Path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    summary = build_monthly_summary(decisions, positions)
    buy_row = summary[summary["recommendation"] == "BUY"].iloc[0]
    # Fixture: 2 BUYs (AAPL, NVDA), both have closed positions, both profitable
    assert buy_row["n_closed"] == 2
    assert buy_row["win_rate"] == 1.0
    # mean of 9.05% and 14.56% = 11.805%
    assert abs(buy_row["mean_realized_pnl_pct"] - 11.805) < 0.01


def test_monthly_summary_handles_zero_closed(snapshot_db: Path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    summary = build_monthly_summary(decisions, positions)
    watch_row = summary[summary["recommendation"] == "WATCH"].iloc[0]
    # WATCH (JPM) has no desk_position — win_rate should be None/NaN, not crash
    assert watch_row["n_closed"] == 0
    assert pd.isna(watch_row["win_rate"])


def test_headline_stats_returns_string_dict(snapshot_db: Path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    stats = compute_headline_stats(decisions, positions, as_of="2026-05-24", since_days=30)
    # Required keys for the README template
    for key in (
        "as_of", "window_start", "window_end", "total_decisions",
        "n_buy", "n_buy_limit", "n_watch", "n_avoid",
        "n_positions_total", "n_positions_closed", "n_positions_open",
        "overall_win_rate", "mean_realized_pnl_pct",
    ):
        assert key in stats, f"missing key {key}"
        assert isinstance(stats[key], str), f"{key} must be a pre-formatted string for the template"
