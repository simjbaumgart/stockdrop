"""Tests for Jinja2 template rendering."""
from __future__ import annotations

from app.services.snapshot.render import render_data_readme, render_package_readme


def _stub_headline_stats():
    return {
        "as_of": "2026-05-24",
        "window_start": "2026-04-24",
        "window_end": "2026-05-24",
        "total_decisions": "404",
        "n_buy": "57",
        "n_buy_limit": "50",
        "n_watch": "62",
        "n_avoid": "191",
        "n_positions_total": "45",
        "n_positions_closed": "20",
        "n_positions_open": "25",
        "overall_win_rate": "55.0%",
        "mean_realized_pnl_pct": "+3.20%",
    }


def test_package_readme_renders_stats():
    text = render_package_readme(_stub_headline_stats())
    assert "404 decisions" in text
    assert "BUY: 57" in text
    assert "Win rate (closed) | 55.0%" in text
    assert "2026-04-24 → 2026-05-24" in text


def test_package_readme_links_charts_and_cases():
    text = render_package_readme(_stub_headline_stats())
    for path in (
        "charts/verdict-distribution.png",
        "charts/sector-breakdown.png",
        "charts/pnl-distribution.png",
        "charts/score-vs-outcome.png",
        "case-studies/01-best-trade.md",
        "case-studies/04-still-open.md",
        "data/decisions.csv",
        "data/schema.sql",
    ):
        assert path in text, f"missing link: {path}"


def test_data_readme_renders_counts():
    text = render_data_readme(
        as_of="2026-05-24",
        since_days=30,
        n_decisions=404,
        n_positions=45,
        n_summary=4,
    )
    assert "404" in text
    assert "45" in text
    assert "subscribers" in text  # explained in the "excluded" section
