"""Deterministic post-check on PM stop-loss placement.

Rule: if stop > (entry_low - 1.5 * ATR), widen the stop to the farther of:
    (a) entry_low - 2.0 * ATR
    (b) the nearest lower SMA (SMA_50 or SMA_200) that sits below entry_low
"""

import pytest

from app.utils.stop_loss_guard import (
    widen_stop_if_too_tight,
    evaluate_stop_acceptability,
    MAX_ACCEPTABLE_DOWNSIDE_PCT,
)


def test_stop_already_far_enough_is_returned_unchanged():
    result = widen_stop_if_too_tight(
        stop_loss=85.0,
        entry_low=100.0,
        atr=5.0,
        sma_50=90.0,
        sma_200=80.0,
        bb_lower=88.0,
    )
    assert result.adjusted is False
    assert result.stop_loss == 85.0
    assert result.reason == "within_tolerance"


def test_stop_too_tight_widens_to_farther_of_atr_or_sma():
    # entry 100, ATR 5 → 1.5 * ATR = 7.5 → threshold 92.5. Stop 95 is too tight.
    # Candidates: 2x ATR = 90.0; SMA_50 = 88.0; SMA_200 = 80.0.
    # Nearest SMA below entry_low is SMA_50 at 88.0.
    # Farther (lower) of {90.0, 88.0} = 88.0.
    result = widen_stop_if_too_tight(
        stop_loss=95.0,
        entry_low=100.0,
        atr=5.0,
        sma_50=88.0,
        sma_200=80.0,
        bb_lower=93.0,
    )
    assert result.adjusted is True
    assert result.stop_loss == 88.0
    assert "sma" in result.reason.lower()


def test_stop_too_tight_uses_2x_atr_when_no_sma_below_entry():
    result = widen_stop_if_too_tight(
        stop_loss=96.0,
        entry_low=100.0,
        atr=5.0,
        sma_50=110.0,
        sma_200=105.0,
        bb_lower=99.0,
    )
    assert result.adjusted is True
    assert result.stop_loss == 90.0  # 100 - 2*5
    assert "atr" in result.reason.lower()


def test_missing_atr_is_noop():
    result = widen_stop_if_too_tight(
        stop_loss=95.0, entry_low=100.0, atr=0.0,
        sma_50=90.0, sma_200=80.0, bb_lower=93.0,
    )
    assert result.adjusted is False
    assert result.reason == "missing_atr"


def test_none_stop_is_noop():
    result = widen_stop_if_too_tight(
        stop_loss=None, entry_low=100.0, atr=5.0,
        sma_50=90.0, sma_200=80.0, bb_lower=93.0,
    )
    assert result.adjusted is False
    assert result.stop_loss is None
    assert result.reason == "missing_stop"


def test_none_atr_is_noop():
    result = widen_stop_if_too_tight(
        stop_loss=95.0, entry_low=100.0, atr=None,
        sma_50=90.0, sma_200=80.0, bb_lower=93.0,
    )
    assert result.adjusted is False
    assert result.reason == "missing_atr"


def test_2x_atr_picks_atr_when_atr_is_lower_than_nearest_sma():
    # entry 100, ATR 10 -> 2*ATR floor = 80; sma_50 = 95 (below entry), sma_200 = 70.
    # Nearest SMA below entry = max(95, 70) = 95.
    # Pick the farther (lower) of 80 and 95 -> 80.
    result = widen_stop_if_too_tight(
        stop_loss=98.0, entry_low=100.0, atr=10.0,
        sma_50=95.0, sma_200=70.0, bb_lower=92.0,
    )
    assert result.adjusted is True
    assert result.stop_loss == 80.0
    assert "atr" in result.reason.lower()


def test_acceptable_when_downside_within_ceiling():
    result = evaluate_stop_acceptability(entry_low=100.0, stop_loss=92.0)
    # downside = 8% — well within ceiling
    assert result.acceptable is True
    assert result.downside_pct == pytest.approx(8.0, abs=0.1)


def test_rejected_when_downside_exceeds_ceiling():
    # entry 100.0, stop 49.0 → -51% downside, exceeds new 50% ceiling
    result = evaluate_stop_acceptability(entry_low=100.0, stop_loss=49.0)
    assert result.acceptable is False
    assert result.downside_pct > MAX_ACCEPTABLE_DOWNSIDE_PCT
    assert "exceeds" in result.reason.lower() or "ceiling" in result.reason.lower()


def test_none_values_are_acceptable():
    # If we don't have data, do not reject.
    assert evaluate_stop_acceptability(entry_low=None, stop_loss=10.0).acceptable is True
    assert evaluate_stop_acceptability(entry_low=10.0, stop_loss=None).acceptable is True


def test_rr_below_floor_is_rejected():
    """R/R 0.2x with mild downside should still reject on the R/R gate."""
    result = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=90.0, risk_reward_ratio=0.2
    )
    assert not result.acceptable
    assert "R/R" in result.reason


def test_rr_exactly_at_floor_is_accepted():
    """R/R 0.3x exactly (strict <) should be accepted."""
    result = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=90.0, risk_reward_ratio=0.3
    )
    assert result.acceptable


def test_rr_above_floor_with_moderate_downside_accepted():
    """R/R 0.5x, -20% downside passes under new rules (would have failed old)."""
    result = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=80.0, risk_reward_ratio=0.5
    )
    assert result.acceptable


def test_asymmetric_high_rr_trade_accepted():
    """Motivating case: 3.0x R/R with -40% downside is now publishable."""
    result = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=60.0, risk_reward_ratio=3.0
    )
    assert result.acceptable


def test_downside_backstop_fires_even_with_high_rr():
    """5.0x R/R, -60% downside → reject on backstop, not R/R."""
    result = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=40.0, risk_reward_ratio=5.0
    )
    assert not result.acceptable
    assert "downside" in result.reason.lower()


def test_rr_none_skips_rr_gate():
    """When R/R is None, only the downside backstop applies."""
    ok = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=80.0, risk_reward_ratio=None
    )
    assert ok.acceptable
    rej = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=40.0, risk_reward_ratio=None
    )
    assert not rej.acceptable


def test_dataclass_carries_rr_through():
    """The returned dataclass should expose the R/R that was evaluated."""
    result = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=90.0, risk_reward_ratio=1.5
    )
    assert result.risk_reward_ratio == 1.5
