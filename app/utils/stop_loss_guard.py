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
from typing import Dict, Optional


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


# Maximum downside% (entry → stop) we're willing to publish as a real trade.
# Beyond this, the widened stop is effectively no stop at all — a hard
# portfolio-risk backstop, applied regardless of R/R.
MAX_ACCEPTABLE_DOWNSIDE_PCT = 50.0

# Minimum R/R below which we treat the PM output as mathematically broken
# and refuse to publish it as a BUY (historical offenders: AAOI 0.2x,
# FORM 0.2x, VICR 0.1x). The R/R floor is the PRIMARY gate; the downside
# ceiling above is only a catastrophic-widen backstop.
MIN_ACCEPTABLE_RR = 0.3


@dataclass
class StopAcceptability:
    acceptable: bool
    downside_pct: Optional[float]
    risk_reward_ratio: Optional[float]
    reason: str


def evaluate_stop_acceptability(
    entry_low: Optional[float],
    stop_loss: Optional[float],
    risk_reward_ratio: Optional[float] = None,
) -> StopAcceptability:
    """Two-gate acceptability check on a (possibly post-widen) trade.

    Gates (any failure → reject):
        1. Downside backstop: reject if downside_pct > MAX_ACCEPTABLE_DOWNSIDE_PCT
           (50%). Always applied when entry_low and stop_loss are present.
        2. R/R primary gate: reject if risk_reward_ratio < MIN_ACCEPTABLE_RR
           (0.3x). Only applied when risk_reward_ratio is provided
           (None → R/R gate skipped, legacy behavior).

    Conservative defaults: when entry_low or stop_loss is None / invalid, we
    accept and return reason="insufficient_data". Caller has nothing better
    to do.

    Boundary semantics:
        - Downside 50.0% exactly → accept (strict `>`)
        - R/R 0.3x exactly → accept (strict `<`)
    """
    if entry_low is None or stop_loss is None or entry_low <= 0:
        return StopAcceptability(True, None, risk_reward_ratio, "insufficient_data")
    downside_pct = abs(entry_low - stop_loss) / entry_low * 100.0
    if downside_pct > MAX_ACCEPTABLE_DOWNSIDE_PCT:
        return StopAcceptability(
            False,
            downside_pct,
            risk_reward_ratio,
            f"downside {downside_pct:.1f}% exceeds ceiling {MAX_ACCEPTABLE_DOWNSIDE_PCT:.1f}%",
        )
    if risk_reward_ratio is not None and risk_reward_ratio < MIN_ACCEPTABLE_RR:
        return StopAcceptability(
            False,
            downside_pct,
            risk_reward_ratio,
            f"R/R {risk_reward_ratio:.1f}x below floor {MIN_ACCEPTABLE_RR:.1f}x",
        )
    return StopAcceptability(True, downside_pct, risk_reward_ratio, "within_acceptable")


def recompute_risk_metrics(
    *,
    entry_low: Optional[float],
    stop_loss: Optional[float],
    upside_percent: Optional[float],
) -> Dict[str, Optional[float]]:
    """Recompute downside_risk_percent and risk_reward_ratio from a
    (entry_low, stop_loss) pair after the stop-guard may have widened the stop.

    Returns a dict with keys 'downside_risk_percent' (rounded to 2dp) and
    'risk_reward_ratio' (rounded to 1dp). Either may be None if inputs are
    missing or invalid (stop >= entry, missing values, etc.).
    """
    if entry_low is None or stop_loss is None or entry_low <= 0:
        return {"downside_risk_percent": None, "risk_reward_ratio": None}
    if stop_loss > entry_low:
        return {"downside_risk_percent": None, "risk_reward_ratio": None}

    downside = round((entry_low - stop_loss) / entry_low * 100.0, 2)
    if downside <= 0 or upside_percent is None:
        return {"downside_risk_percent": downside, "risk_reward_ratio": None}

    rr = round(float(upside_percent) / downside, 1)
    return {"downside_risk_percent": downside, "risk_reward_ratio": rr}
