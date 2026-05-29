"""Tests for app/services/analytics/dr_compare_metrics.py

Step 3a — TDD: these tests are written before the implementation.
"""
import math
import pytest

from app.services.analytics.dr_compare_metrics import (
    SENTINEL_VERDICTS,
    confusion_matrix,
    cohens_kappa,
    verdict_agreement,
    action_agreement,
)


# ─── SENTINEL_VERDICTS ────────────────────────────────────────────────────────

def test_sentinel_verdicts_contains_expected():
    assert "ERROR_PARSING" in SENTINEL_VERDICTS
    assert "INCOMPLETE_TRADING_LEVELS" in SENTINEL_VERDICTS


# ─── confusion_matrix ─────────────────────────────────────────────────────────

def test_confusion_matrix_simple():
    pairs = [
        ("A", "A"),
        ("A", "B"),
        ("B", "B"),
    ]
    cm = confusion_matrix(pairs)
    assert cm["matrix"]["A"]["A"] == 1
    assert cm["matrix"]["A"]["B"] == 1
    assert cm["matrix"]["B"]["B"] == 1
    assert cm["matrix"]["B"]["A"] == 0


def test_confusion_matrix_label_set_complete():
    pairs = [("A", "B"), ("B", "A"), ("A", "A")]
    cm = confusion_matrix(pairs)
    assert set(cm["labels"]) == {"A", "B"}


def test_confusion_matrix_empty_input():
    cm = confusion_matrix([])
    assert cm["matrix"] == {}
    assert cm["labels"] == []


def test_confusion_matrix_single_label():
    pairs = [("X", "X"), ("X", "X")]
    cm = confusion_matrix(pairs)
    assert cm["matrix"]["X"]["X"] == 2
    assert cm["labels"] == ["X"]


# ─── cohens_kappa ─────────────────────────────────────────────────────────────

def test_cohens_kappa_perfect_agreement():
    pairs = [("A", "A"), ("B", "B"), ("C", "C"), ("A", "A")]
    kappa = cohens_kappa(pairs)
    assert abs(kappa - 1.0) < 1e-9


def test_cohens_kappa_chance_level():
    """With perfectly balanced off-diagonal disagreement, κ should be near 0."""
    # 2 labels, 50/50 base rates; gemini always says A, claude always says B →
    # po = 0, pe = 0.5*0.5 + 0.5*0.5 = 0.5, κ = (0-0.5)/(1-0.5) = -1.0
    # For "near 0" we use a genuinely random-ish arrangement instead.
    # Build a case where po ≈ pe: symmetric disagreement.
    # gemini=[A,A,B,B], claude=[A,B,A,B] → po=0.5, pe=(0.5*0.5+0.5*0.5)=0.5 → κ=0
    pairs = [("A", "A"), ("A", "B"), ("B", "A"), ("B", "B")]
    kappa = cohens_kappa(pairs)
    assert abs(kappa - 0.0) < 1e-9


def test_cohens_kappa_hand_computed():
    """
    2-class example: gemini=[A,A,A,B], claude=[A,A,B,B]
    Confusion:        claude-A  claude-B
      gemini-A  [ 2       1  ]
      gemini-B  [ 0       1  ]
    po = (2+1)/4 = 0.75
    p_gem(A)=3/4, p_gem(B)=1/4
    p_cl(A)=2/4=0.5, p_cl(B)=2/4=0.5
    pe = (3/4)*(2/4) + (1/4)*(2/4) = 6/16 + 2/16 = 8/16 = 0.5
    κ = (0.75 - 0.5) / (1 - 0.5) = 0.5
    """
    pairs = [("A", "A"), ("A", "A"), ("A", "B"), ("B", "B")]
    kappa = cohens_kappa(pairs)
    assert abs(kappa - 0.5) < 1e-9


def test_cohens_kappa_degenerate_single_category():
    """When pe == 1 (all in one category), κ = 0.0 — no division by zero."""
    pairs = [("X", "X"), ("X", "X"), ("X", "X")]
    kappa = cohens_kappa(pairs)
    assert kappa == 0.0


def test_cohens_kappa_empty_input():
    """Empty input returns nan or 0.0; must not raise."""
    kappa = cohens_kappa([])
    # accepts either documented return value
    assert kappa == 0.0 or math.isnan(kappa)


# ─── verdict_agreement ────────────────────────────────────────────────────────

def test_verdict_agreement_excludes_sentinels():
    rows = [
        {"gemini_verdict": "ADJUSTED", "claude_verdict": "ADJUSTED"},
        {"gemini_verdict": "ERROR_PARSING", "claude_verdict": "ADJUSTED"},
        {"gemini_verdict": "INCOMPLETE_TRADING_LEVELS", "claude_verdict": "ADJUSTED"},
        {"gemini_verdict": "OVERRIDDEN", "claude_verdict": "OVERRIDDEN"},
    ]
    result = verdict_agreement(rows)
    assert result["n_excluded"] == 2
    assert result["n"] == 2  # only non-sentinel rows


def test_verdict_agreement_n_excluded_zero_when_no_sentinels():
    rows = [
        {"gemini_verdict": "ADJUSTED", "claude_verdict": "ADJUSTED"},
        {"gemini_verdict": "OVERRIDDEN", "claude_verdict": "ADJUSTED"},
    ]
    result = verdict_agreement(rows)
    assert result["n_excluded"] == 0
    assert result["n"] == 2


def test_verdict_agreement_raw_agreement():
    rows = [
        {"gemini_verdict": "ADJUSTED", "claude_verdict": "ADJUSTED"},
        {"gemini_verdict": "ADJUSTED", "claude_verdict": "OVERRIDDEN"},
    ]
    result = verdict_agreement(rows)
    assert abs(result["raw_agreement"] - 0.5) < 1e-9


def test_verdict_agreement_contains_required_keys():
    rows = [{"gemini_verdict": "ADJUSTED", "claude_verdict": "ADJUSTED"}]
    result = verdict_agreement(rows)
    for key in ("n", "n_excluded", "raw_agreement", "kappa", "confusion"):
        assert key in result, f"missing key: {key}"


def test_verdict_agreement_empty_rows():
    result = verdict_agreement([])
    assert result["n"] == 0
    assert result["n_excluded"] == 0
    assert result["raw_agreement"] == 0.0


def test_verdict_agreement_all_excluded():
    rows = [
        {"gemini_verdict": "ERROR_PARSING", "claude_verdict": "ADJUSTED"},
        {"gemini_verdict": "INCOMPLETE_TRADING_LEVELS", "claude_verdict": "ADJUSTED"},
    ]
    result = verdict_agreement(rows)
    assert result["n"] == 0
    assert result["n_excluded"] == 2
    assert result["raw_agreement"] == 0.0


# ─── action_agreement ─────────────────────────────────────────────────────────

def test_action_agreement_no_sentinel_exclusion():
    rows = [
        {"gemini_action": "BUY", "claude_action": "BUY"},
        {"gemini_action": "BUY_LIMIT", "claude_action": "AVOID"},
    ]
    result = action_agreement(rows)
    assert result["n_excluded"] == 0
    assert result["n"] == 2


def test_action_agreement_contains_required_keys():
    rows = [{"gemini_action": "BUY", "claude_action": "BUY"}]
    result = action_agreement(rows)
    for key in ("n", "n_excluded", "raw_agreement", "kappa", "confusion"):
        assert key in result, f"missing key: {key}"


def test_action_agreement_raw_agreement():
    rows = [
        {"gemini_action": "BUY", "claude_action": "BUY"},
        {"gemini_action": "BUY", "claude_action": "AVOID"},
        {"gemini_action": "AVOID", "claude_action": "AVOID"},
        {"gemini_action": "AVOID", "claude_action": "BUY"},
    ]
    result = action_agreement(rows)
    assert abs(result["raw_agreement"] - 0.5) < 1e-9


def test_action_agreement_empty_rows():
    result = action_agreement([])
    assert result["n"] == 0
    assert result["n_excluded"] == 0
