"""Tests for the candidate/pinned calibration-card split (Tier 2).

The nightly builder writes a *candidate* card (console-only); prompts read
only the hand-pinned card produced by scripts/analysis/pin_calibration_card.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


def _mk_row(**over):
    row = {
        "drop_type": "EARNINGS_MISS",
        "is_earnings_drop": 1,
        "recommendation": "WATCH",
        "deep_research_verdict": "HOLD_OFF",
        "gatekeeper_tier": "STANDARD_DIP",
        "ret_1w": -0.01,
        "ret_4w": 0.02,
        "recovered_4w": 1,
    }
    row.update(over)
    return row


def test_builder_writes_candidate_paths(tmp_path, monkeypatch):
    from scripts.analysis import build_calibration_card as bcc

    assert os.path.basename(bcc.JSON_PATH) == "calibration_card_candidate.json"
    assert os.path.basename(bcc.MD_PATH) == "calibration_card_candidate.md"

    rows = [_mk_row() for _ in range(25)] + [
        _mk_row(drop_type="SECTOR_ROTATION", is_earnings_drop=0) for _ in range(25)
    ]
    monkeypatch.setattr(bcc, "get_outcomes_joined", lambda: rows)
    monkeypatch.setattr(bcc, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bcc, "JSON_PATH", str(tmp_path / "calibration_card_candidate.json"))
    monkeypatch.setattr(bcc, "MD_PATH", str(tmp_path / "calibration_card_candidate.md"))

    card = bcc.run(min_n=20)
    assert (tmp_path / "calibration_card_candidate.json").exists()
    assert (tmp_path / "calibration_card_candidate.md").exists()
    assert card["by_earnings"]["earnings"]["n"] == 25
