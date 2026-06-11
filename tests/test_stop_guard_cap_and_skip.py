# tests/test_stop_guard_cap_and_skip.py
"""v0.8.2-288 review #3 (AAOI): the stop-guard widened 137.38 -> 73.15 via
SMA200 on an AVOID verdict, publishing -52% downside / R/R 0.1. Two rules:
the guard only runs for buy-side verdicts, and SMA widening is capped at
3x ATR below entry."""

from app.utils.stop_loss_guard import widen_stop_if_too_tight, should_run_stop_guard


def test_sma_widening_capped_at_3x_atr():
    # entry 137, ATR 5 -> hard floor 122. SMA200 way below at 73.15 must NOT win.
    adj = widen_stop_if_too_tight(
        stop_loss=136.0, entry_low=137.0, atr=5.0,
        sma_50=None, sma_200=73.15, bb_lower=None,
    )
    assert adj.adjusted
    assert adj.stop_loss == 122.0          # entry - 3*ATR, not the distant SMA
    assert adj.reason == "capped_at_3x_atr"


def test_sma_within_cap_still_used():
    # SMA200 at 126 sits between 2x (127) and 3x (122) ATR floors -> keep SMA.
    adj = widen_stop_if_too_tight(
        stop_loss=136.0, entry_low=137.0, atr=5.0,
        sma_50=None, sma_200=126.0, bb_lower=None,
    )
    assert adj.adjusted
    assert adj.stop_loss == 126.0
    assert adj.reason == "widened_to_sma_200"


def test_plain_2x_atr_widen_unaffected():
    adj = widen_stop_if_too_tight(
        stop_loss=136.0, entry_low=137.0, atr=5.0,
        sma_50=None, sma_200=None, bb_lower=None,
    )
    assert adj.stop_loss == 127.0
    assert adj.reason == "widened_to_2x_atr"


def test_should_run_stop_guard_only_for_buys():
    assert should_run_stop_guard("BUY")
    assert should_run_stop_guard("BUY_LIMIT")
    assert should_run_stop_guard("buy_limit")    # case-insensitive
    assert not should_run_stop_guard("AVOID")
    assert not should_run_stop_guard("WATCH")
    assert not should_run_stop_guard("")
    assert not should_run_stop_guard(None)
