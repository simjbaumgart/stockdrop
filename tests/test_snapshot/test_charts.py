"""Tests for the four PNG chart renderers."""
from __future__ import annotations

from pathlib import Path

from app.services.snapshot.charts import (
    chart_pnl_distribution,
    chart_score_vs_outcome,
    chart_sector_breakdown,
    chart_verdict_distribution,
)
from app.services.snapshot.db_export import load_decisions, load_positions


def _assert_png(path: Path):
    assert path.exists(), f"{path} not written"
    assert path.stat().st_size > 1000, f"{path} suspiciously small"
    # PNG signature: 0x89 'PNG\r\n\x1a\n'
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"


def test_chart_verdict_distribution(snapshot_db: Path, tmp_path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    out = tmp_path / "verdict.png"
    chart_verdict_distribution(decisions, out)
    _assert_png(out)


def test_chart_sector_breakdown(snapshot_db: Path, tmp_path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    out = tmp_path / "sector.png"
    chart_sector_breakdown(decisions, out)
    _assert_png(out)


def test_chart_pnl_distribution(snapshot_db: Path, tmp_path):
    positions = load_positions(snapshot_db)
    out = tmp_path / "pnl.png"
    chart_pnl_distribution(positions, out)
    _assert_png(out)


def test_chart_score_vs_outcome(snapshot_db: Path, tmp_path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    out = tmp_path / "scatter.png"
    chart_score_vs_outcome(decisions, positions, out)
    _assert_png(out)


def test_chart_handles_empty_input(tmp_path):
    """Should write a placeholder PNG rather than crash on empty data."""
    import pandas as pd
    out = tmp_path / "empty.png"
    chart_verdict_distribution(pd.DataFrame(columns=["recommendation"]), out)
    _assert_png(out)
