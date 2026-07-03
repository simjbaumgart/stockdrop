"""Backfill decision_outcomes from yfinance historical closes. Idempotent.

Forward returns are deterministic functions of historical daily closes, so we
reconstruct them per decision instead of depending on the (never-scheduled)
live decision_tracking writer. This is Phase 0 of the calibration feedback loop
(docs/proposals/PLAN_calibration_feedback_option1.md).

Design choices (locked with Simon):
  * Label horizon for the card is 4w, but all of 1w/2w/4w/8w are stored.
  * Baseline = the decision's own ``price_at_decision`` when present (closest to
    the real fill), falling back to the first session close on/after the date.
  * ``auto_adjust=True`` so a mid-window stock split does not create a spurious
    discontinuity. The small residual dividend-basis drift between a raw
    ``price_at_decision`` and adjusted closes (~<1% over 8 weeks) is accepted
    noise for base-rate buckets.

Usage:
    python -m scripts.maintenance.backfill_outcomes [--dry-run] [--limit N]
"""
import argparse
import os
import sys
from typing import List, Optional

import pandas as pd
import yfinance as yf

# Allow running as a standalone script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.database import (  # noqa: E402
    get_decision_points,
    upsert_decision_outcome,
)

# Horizons in trading days.
HORIZONS = {"1w": 5, "2w": 10, "4w": 20, "8w": 40}


def _fwd_return(closes: pd.Series, baseline: float, td: int) -> Optional[float]:
    """Return at ``td`` trading days from the first session, vs ``baseline``."""
    if baseline is None or baseline <= 0:
        return None
    if td >= len(closes):
        return None
    p1 = closes.iloc[td]
    if p1 is None or pd.isna(p1):
        return None
    return float(p1) / float(baseline) - 1.0


def compute_outcome_fields(symbol: str, decision_date: str,
                           price_at_decision: Optional[float]) -> Optional[dict]:
    """Pull closes from ``decision_date`` forward and compute outcome fields.

    Returns the dict of columns to upsert into decision_outcomes, or None when
    yfinance has no usable data for the symbol/date.
    """
    try:
        hist = yf.Ticker(symbol).history(start=decision_date, period="3mo",
                                          auto_adjust=True)
    except Exception as e:  # network / delisting / bad symbol
        print(f"  ! {symbol} @ {decision_date}: yfinance error: {e}")
        return None
    if hist is None or hist.empty or "Close" not in hist:
        return None
    closes = hist["Close"].dropna()
    if closes.empty:
        return None

    # Baseline: recorded decision price when present, else first session close.
    first_close = float(closes.iloc[0])
    if price_at_decision and price_at_decision > 0:
        baseline, baseline_source = float(price_at_decision), "price_at_decision"
    else:
        baseline, baseline_source = first_close, "first_close"

    rets = {h: _fwd_return(closes, baseline, td) for h, td in HORIZONS.items()}

    fields = {
        "symbol": symbol,
        "decision_date": decision_date,
        "price_at_decision": price_at_decision,
        "baseline_price": baseline,
        "baseline_source": baseline_source,
    }
    for h in HORIZONS:
        fields[f"ret_{h}"] = (round(rets[h], 6) if rets[h] is not None else None)
        fields[f"recovered_{h}"] = (int(rets[h] >= 0) if rets[h] is not None else None)

    # Largest matured horizon ('complete' once 8w is in).
    if rets["8w"] is not None:
        fields["last_filled_horizon"] = "complete"
    else:
        filled = [h for h in HORIZONS if rets[h] is not None]
        fields["last_filled_horizon"] = filled[-1] if filled else None
    return fields


def run(dry_run: bool = False, limit: Optional[int] = None,
        decisions: Optional[List[dict]] = None) -> dict:
    """Backfill / forward-mark outcomes. Returns a small summary dict.

    ``decisions`` lets callers (e.g. the live marking job) pass a pre-filtered
    subset; defaults to every decision_point.
    """
    if decisions is None:
        decisions = get_decision_points()
    if limit:
        decisions = decisions[:limit]

    processed = upserts = skipped = 0
    for d in decisions:
        sym = d.get("symbol")
        ts = d.get("timestamp") or ""
        dec_date = ts[:10]
        if not sym or not dec_date:
            skipped += 1
            continue
        processed += 1
        fields = compute_outcome_fields(sym, dec_date, d.get("price_at_decision"))
        if not fields:
            skipped += 1
            continue
        if dry_run:
            print(f"  {sym:6s} {dec_date}  "
                  f"1w={fields['ret_1w']} 4w={fields['ret_4w']} "
                  f"8w={fields['ret_8w']} [{fields['last_filled_horizon']}] "
                  f"base={fields['baseline_source']}")
        else:
            if upsert_decision_outcome(d["id"], **fields):
                upserts += 1
    summary = {"processed": processed, "upserts": upserts, "skipped": skipped}
    print(f"[backfill_outcomes] {summary}")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backfill decision_outcomes from yfinance")
    ap.add_argument("--dry-run", action="store_true", help="Print, do not write")
    ap.add_argument("--limit", type=int, help="Only process first N decisions")
    args = ap.parse_args()
    run(dry_run=args.dry_run, limit=args.limit)
