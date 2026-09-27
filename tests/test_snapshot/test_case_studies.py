"""Tests for case-study candidate selection and draft markdown."""
from __future__ import annotations

from pathlib import Path

from app.services.snapshot.case_studies import (
    draft_case_study,
    pick_candidates,
)
from app.services.snapshot.db_export import load_decisions, load_positions


def test_pick_candidates_returns_one_per_slot(snapshot_db: Path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    candidates = pick_candidates(decisions, positions)
    assert set(candidates.keys()) == {"best", "worst", "avoided", "open"}
    # best = NVDA (+14.56%), worst = MSFT (-7.83%) per fixture
    assert candidates["best"]["ticker"] == "NVDA"
    assert candidates["worst"]["ticker"] == "MSFT"


def test_pick_candidates_handles_missing_categories(snapshot_db: Path):
    """If e.g. no AVOID exists in the window, that slot is None — drafter handles it."""
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    # Fixture has AVOIDs, but check the empty-data path explicitly:
    import pandas as pd
    empty = pick_candidates(decisions.iloc[0:0], positions.iloc[0:0])
    for slot in ("best", "worst", "avoided", "open"):
        assert empty[slot] is None


def test_draft_case_study_excludes_llm_text(snapshot_db: Path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    candidates = pick_candidates(decisions, positions)
    md = draft_case_study("best", candidates["best"])
    # Must mention structured fields:
    assert "NVDA" in md
    assert "BUY" in md
    # Must NOT mention LLM-prose-only fields (they weren't even loaded, but guard regardless):
    for banned in ("reasoning", "swot", "blindspots", "verification"):
        assert banned not in md.lower(), f"banned token in draft: {banned}"


def test_draft_case_study_handles_none():
    md = draft_case_study("worst", None)
    assert "No candidate" in md


def test_draft_case_study_renders_nan_as_em_dash():
    """NaN values for string fields (sector, exit_date, exit_reason) must
    render as the em-dash placeholder, never the literal string "nan"."""
    import numpy as np
    candidate = {
        "ticker": "FOO",
        "company_name": "Foo Corp",
        "sector": np.nan,                       # NaN string field
        "drop_percent": -7.5,
        "recommendation": "BUY",
        "ai_score": None,                       # None numeric
        "deep_research_action": np.nan,         # NaN string field
        "deep_research_score": np.nan,          # NaN numeric
        "entry_price_low": 10.0, "entry_price_high": 11.0,
        "stop_loss": 9.0, "take_profit_1": 12.0, "take_profit_2": 13.0,
        "status": "ACTIVE",
        "entry_date": "2026-05-10", "entry_price": 10.5,
        "current_price": 10.8, "unrealized_pnl_pct": 2.86,
        "exit_date": np.nan, "exit_price": np.nan,
        "realized_pnl_pct": np.nan, "exit_reason": np.nan,
    }
    md = draft_case_study("open", candidate)
    assert "nan" not in md.lower(), f"NaN leaked into draft: {md}"
    assert "still open" in md  # exit_date=NaN -> treated as no exit


def test_pick_candidates_prefers_in_window_position(snapshot_db: Path):
    """When a position's decision is outside the time window, the slot's
    setup section would be empty. Prefer in-window candidates."""
    import pandas as pd
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    # Add a fake "even better" closed position whose decision_point_id
    # does NOT appear in the in-window decisions DataFrame (simulating a
    # position from before the window).
    extra = pd.DataFrame([{
        "id": 999, "decision_point_id": 7777,  # 7777 not in decisions.id
        "ticker": "OUTSIDE", "status": "CLOSED",
        "entry_date": "2026-03-01", "entry_price": 100.0,
        "position_size": 1000.0, "attractiveness_score": 80.0,
        "current_price": None, "unrealized_pnl_pct": None,
        "exit_date": "2026-03-15", "exit_price": 150.0,
        "realized_pnl_pct": 50.0,  # bigger than any in-window winner
        "exit_reason": "TP2",
    }])
    augmented = pd.concat([positions, extra], ignore_index=True)
    candidates = pick_candidates(decisions, augmented)
    # NVDA (in-window, +14.56%) should still win over OUTSIDE (+50%, out-of-window)
    assert candidates["best"]["ticker"] == "NVDA"
