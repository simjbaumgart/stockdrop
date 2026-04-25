"""Deterministic post-check on PM stop-loss placement.

Rule: if stop > (entry_low - 1.5 * ATR), widen the stop to the farther of:
    (a) entry_low - 2.0 * ATR
    (b) the nearest lower SMA (SMA_50 or SMA_200) that sits below entry_low
"""

import pytest

from app.utils.stop_loss_guard import widen_stop_if_too_tight


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
