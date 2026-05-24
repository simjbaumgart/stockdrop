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
