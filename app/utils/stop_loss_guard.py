"""Deterministic post-check on PM-generated stop-loss placements.

The PM agent is instructed to set stops at 2*ATR below entry_price_low, but
occasionally returns stops that are too tight (e.g. NMR near the lower
Bollinger band, VRSN in the technical void between SMA_50 and SMA_200).
This helper widens the stop to a defensible floor whenever the PM's value
violates the 1.5*ATR minimum distance rule.

Rule:
    if stop > entry_low - 1.5 * ATR:
        widen to min(entry_low - 2 * ATR, nearest SMA below entry_low)
    where "nearest SMA below entry_low" = max(sma_50, sma_200) restricted to
    those below entry_low.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StopLossAdjustment:
    stop_loss: Optional[float]
    adjusted: bool
    reason: str


def widen_stop_if_too_tight(
    *,
    stop_loss: Optional[float],
    entry_low: float,
    atr: Optional[float],
    sma_50: Optional[float],
    sma_200: Optional[float],
    bb_lower: Optional[float],
) -> StopLossAdjustment:
    """Post-check the PM's stop-loss and widen it if it violates the 1.5*ATR rule.

    Args:
        stop_loss: The PM's proposed stop-loss price.
        entry_low: The lower bound of the PM's entry price zone.
        atr: Average True Range from TradingView indicators (key: 'atr').
        sma_50: 50-day SMA from TradingView indicators (key: 'sma50').
        sma_200: 200-day SMA from TradingView indicators (key: 'sma200').
        bb_lower: Lower Bollinger Band (key: 'bb_lower'). Reserved for future use.

    Returns:
        StopLossAdjustment dataclass with the (possibly widened) stop_loss,
        a boolean indicating whether an adjustment was made, and a reason string.
    """
    if stop_loss is None:
        return StopLossAdjustment(stop_loss=None, adjusted=False, reason="missing_stop")
    if not atr or atr <= 0:
        return StopLossAdjustment(stop_loss=stop_loss, adjusted=False, reason="missing_atr")

    tolerance = entry_low - 1.5 * atr
    if stop_loss <= tolerance:
        return StopLossAdjustment(
            stop_loss=stop_loss, adjusted=False, reason="within_tolerance",
        )

    # Candidate 1: 2x ATR below entry
    atr_floor = entry_low - 2.0 * atr

    # Candidate 2: nearest SMA below entry_low (= max of sub-entry SMAs)
    sma_candidates = [s for s in (sma_50, sma_200) if s is not None and s < entry_low]
    sma_floor = max(sma_candidates) if sma_candidates else None

    if sma_floor is not None:
        # Pick the farther (lower) of the two floors so we don't pull the stop
        # back toward the entry when a nearby SMA is inside 2*ATR.
        new_stop = min(atr_floor, sma_floor)
        if new_stop == sma_floor and sma_floor == sma_50 and (sma_200 is None or sma_50 >= sma_200):
            reason = "widened_to_sma_50"
        elif new_stop == sma_floor and sma_floor == sma_200:
            reason = "widened_to_sma_200"
        else:
            reason = "widened_to_2x_atr"
    else:
        new_stop = atr_floor
        reason = "widened_to_2x_atr"

    return StopLossAdjustment(stop_loss=round(new_stop, 2), adjusted=True, reason=reason)
