"""Tests for the snapshot DB export: time filter, column allowlist, privacy guard."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from app.services.snapshot.db_export import (
    DECISION_POINTS_ALLOWLIST,
    EXCLUDED_TABLES,
    export_snapshot_data,
    load_decisions,
    load_positions,
)


BANNED_LLM_COLUMNS = {
    "reasoning",
    "deep_research_reason",
    "deep_research_swot",
    "deep_research_risk",
    "deep_research_catalyst",
    "deep_research_knife_catch",
    "deep_research_global_analysis",
    "deep_research_local_analysis",
    "deep_research_verification",
    "deep_research_blindspots",
    "reassess_reasoning",
    "deep_research_review_verdict",
}


def test_load_decisions_respects_time_window(snapshot_db: Path):
    df = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    # Fixture has 6 rows in-window, 1 row 45 days ago (TSLA) — that must be excluded.
    assert len(df) == 6
    assert "TSLA" not in df["symbol"].values


def test_load_decisions_only_returns_allowlisted_columns(snapshot_db: Path):
    df = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    extra = set(df.columns) - set(DECISION_POINTS_ALLOWLIST)
    assert not extra, f"unexpected columns: {extra}"
    leaked = BANNED_LLM_COLUMNS.intersection(df.columns)
    assert not leaked, f"LLM free-text columns leaked: {leaked}"


def test_load_decisions_skips_missing_allowlisted_cols_silently(snapshot_db: Path, tmp_path):
    """If the DB doesn't have an allowlisted column (e.g. older schema),
    the export should still succeed — that column just won't be in the output."""
    df = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    # The fixture omits batch_winner; it should not raise.
    assert "batch_winner" not in df.columns


def test_load_positions_returns_all_columns(snapshot_db: Path):
    df = load_positions(snapshot_db)
    # Fixture has 6 positions
    assert len(df) == 6
    # Structured columns we rely on downstream
    for col in ("ticker", "status", "entry_price", "realized_pnl_pct", "current_price"):
        assert col in df.columns


def test_subscribers_table_never_exported(snapshot_db: Path, tmp_path):
    out_dir = tmp_path / "out"
    export_snapshot_data(snapshot_db, out_dir, since_days=30, as_of="2026-05-24")
    # Even if a future caller asks for "all tables", subscribers must be filtered.
    assert not (out_dir / "subscribers.csv").exists()
    assert "subscribers" in EXCLUDED_TABLES


def test_export_writes_expected_files(snapshot_db: Path, tmp_path):
    out_dir = tmp_path / "out"
    export_snapshot_data(snapshot_db, out_dir, since_days=30, as_of="2026-05-24")
    assert (out_dir / "decisions.csv").exists()
    assert (out_dir / "positions.csv").exists()
    assert (out_dir / "schema.sql").exists()
    # schema.sql contains both shipped tables
    schema_text = (out_dir / "schema.sql").read_text()
    assert "CREATE TABLE decision_points" in schema_text
    assert "CREATE TABLE desk_positions" in schema_text
    assert "subscribers" not in schema_text  # subscribers table never appears
