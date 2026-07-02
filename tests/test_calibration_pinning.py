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


def _candidate_card():
    return {
        "as_of": "2026-06-25",
        "min_n": 20,
        "primary_horizon": "4w",
        "total_labeled": 581,
        "by_drop_type": {"EARNINGS_MISS": {"n": 184, "recovery_rate_4w": 0.462,
                                           "mean_ret_4w": 0.0025, "mean_ret_1w": -0.008}},
        "by_earnings": {"earnings": {"n": 184, "recovery_rate_4w": 0.462,
                                     "mean_ret_4w": 0.0025, "mean_ret_1w": -0.008},
                        "non_earnings": {"n": 412, "recovery_rate_4w": 0.549,
                                         "mean_ret_4w": 0.073, "mean_ret_1w": 0.011}},
        "by_pm_verdict": {"AVOID": {"n": 300, "recovery_rate_4w": 0.483,
                                    "mean_ret_4w": 0.0426, "mean_ret_1w": 0.004}},
        "by_dr_verdict": {"BUY_NOW": {"n": 60, "recovery_rate_4w": 0.55,
                                      "mean_ret_4w": 0.05, "mean_ret_1w": 0.01}},
        "by_gatekeeper_tier": {"DEEP_DIP": {"n": 100, "recovery_rate_4w": 0.5,
                                            "mean_ret_4w": 0.03, "mean_ret_1w": 0.0}},
    }


def test_validate_candidate_accepts_good_card():
    from scripts.analysis.pin_calibration_card import validate_candidate
    assert validate_candidate(_candidate_card()) == []


def test_validate_candidate_rejects_thin_or_broken_cards():
    from scripts.analysis.pin_calibration_card import validate_candidate
    no_earnings = _candidate_card()
    no_earnings["by_earnings"] = {}
    assert any("by_earnings" in e for e in validate_candidate(no_earnings))

    low_min_n = _candidate_card()
    low_min_n["min_n"] = 5
    assert any("min_n" in e for e in validate_candidate(low_min_n))

    thin = _candidate_card()
    thin["total_labeled"] = 40
    assert any("total_labeled" in e for e in validate_candidate(thin))


def test_build_pinned_strips_verdict_sections_and_stamps_audit():
    from scripts.analysis.pin_calibration_card import build_pinned
    pinned = build_pinned(_candidate_card(), audited_at="2026-06-30", git_ref="abc1234")
    assert "by_pm_verdict" not in pinned
    assert "by_dr_verdict" not in pinned
    assert "by_gatekeeper_tier" not in pinned
    assert pinned["audited_at"] == "2026-06-30"
    assert pinned["pinned_git_ref"] == "abc1234"
    assert pinned["by_earnings"]["non_earnings"]["n"] == 412


def test_pin_run_requires_approve(tmp_path, monkeypatch):
    import scripts.analysis.pin_calibration_card as pin
    candidate = tmp_path / "calibration_card_candidate.json"
    pinned = tmp_path / "calibration_card_pinned.json"
    candidate.write_text(json.dumps(_candidate_card()))
    monkeypatch.setattr(pin, "CANDIDATE_JSON", str(candidate))
    monkeypatch.setattr(pin, "PINNED_JSON", str(pinned))

    assert pin.run(approve=False, audited_at="2026-06-30") == 0   # dry-run
    assert not pinned.exists()
    assert pin.run(approve=True, audited_at="2026-06-30") == 0
    assert json.loads(pinned.read_text())["audited_at"] == "2026-06-30"
