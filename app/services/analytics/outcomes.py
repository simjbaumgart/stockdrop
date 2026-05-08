"""Compute returns, drawdowns, and recovery times from cached OHLC bars."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

HORIZON_DAYS = {"1w": 5, "2w": 10, "4w": 20, "8w": 40}


def _bars_after(decision_date: pd.Timestamp, bars: pd.DataFrame) -> pd.DataFrame:
    """Bars on or after decision_date, sorted ascending."""
    if bars is None or bars.empty:
        return pd.DataFrame()
    bars = bars.sort_index()
    return bars.loc[bars.index >= decision_date]


def compute_outcome(
    decision_price: float,
    decision_date: pd.Timestamp,
    bars: pd.DataFrame,
    pre_drop_price: Optional[float] = None,
) -> dict:
    """
    Compute horizon returns, max ROI, max drawdown, recovery time for one decision.
    Returns NaN for any horizon where insufficient bars exist.
    """
    out = {f"return_{h}": np.nan for h in HORIZON_DAYS}
    out.update({
        "max_roi_4w": np.nan,
        "max_roi_8w": np.nan,
        "max_drawdown_4w": np.nan,
        "recovered": False,
        "days_to_recover": np.nan,
    })

    forward = _bars_after(decision_date, bars)
    if forward.empty or decision_price is None or decision_price <= 0:
        return out

    closes = forward["Close"].astype(float)
    highs = forward["High"].astype(float) if "High" in forward.columns else closes
    lows = forward["Low"].astype(float) if "Low" in forward.columns else closes

    for label, n in HORIZON_DAYS.items():
        if len(closes) > n:
            out[f"return_{label}"] = float((closes.iloc[n] - decision_price) / decision_price)

    if len(highs) > 0:
        window_4w = highs.iloc[: HORIZON_DAYS["4w"] + 1]
        window_8w = highs.iloc[: HORIZON_DAYS["8w"] + 1]
        if len(window_4w) > 1:
            out["max_roi_4w"] = float((window_4w.max() - decision_price) / decision_price)
        if len(window_8w) > 1:
            out["max_roi_8w"] = float((window_8w.max() - decision_price) / decision_price)

    if len(lows) > 0:
        window_4w_lows = lows.iloc[: HORIZON_DAYS["4w"] + 1]
        if len(window_4w_lows) > 1:
            out["max_drawdown_4w"] = float((window_4w_lows.min() - decision_price) / decision_price)

    if pre_drop_price is not None and pre_drop_price > 0:
        window = highs.iloc[: HORIZON_DAYS["8w"] + 1]
        hit = window[window >= pre_drop_price]
        if not hit.empty:
            out["recovered"] = True
            first_hit_idx = hit.index[0]
            day_offsets = int((forward.index <= first_hit_idx).sum() - 1)
            out["days_to_recover"] = day_offsets

    return out


def _simulate_buy_limit_fill(row: pd.Series, bars: pd.DataFrame):
    """Return (filled, cost_basis). cost_basis is entry midpoint if filled, else None."""
    lo, hi = row.get("entry_price_low"), row.get("entry_price_high")
    if pd.isna(lo) or pd.isna(hi):
        return False, None
    forward = _bars_after(row["decision_date"], bars)
    if forward.empty:
        return False, None
    window = forward.iloc[: HORIZON_DAYS["4w"] + 1]
    if "Low" not in window.columns or "High" not in window.columns:
        return False, None
    touched = (window["Low"].astype(float) <= float(hi)) & (window["High"].astype(float) >= float(lo))
    if touched.any():
        return True, float((float(lo) + float(hi)) / 2.0)
    return False, None


def enrich_outcomes(cohort: pd.DataFrame, bars_by_ticker: dict) -> pd.DataFrame:
    """
    For each row in cohort, compute outcome columns using the matching bars.
    Adds columns and returns a new DataFrame.
    """
    if cohort.empty:
        return cohort.copy()

    records = []
    for _, row in cohort.iterrows():
        bars = bars_by_ticker.get(str(row["symbol"]).upper(), pd.DataFrame())
        pre_drop = None
        if "pre_drop_price" in row.index and pd.notna(row.get("pre_drop_price")):
            pre_drop = float(row["pre_drop_price"])
        elif pd.notna(row.get("drop_percent")) and float(row.get("drop_percent") or 0) != 0:
            try:
                pre_drop = float(row["price_at_decision"]) / (1.0 + float(row["drop_percent"]) / 100.0)
            except Exception:
                pre_drop = None

        outcome = compute_outcome(
            decision_price=float(row["price_at_decision"]) if pd.notna(row["price_at_decision"]) else 0.0,
            decision_date=row["decision_date"],
            bars=bars,
            pre_drop_price=pre_drop,
        )

        if row.get("intent") == "ENTER_LIMIT":
            filled, cost_basis = _simulate_buy_limit_fill(row, bars)
            outcome["limit_filled"] = filled
            outcome["limit_cost_basis"] = cost_basis if cost_basis is not None else np.nan
            if filled and cost_basis:
                forward = _bars_after(row["decision_date"], bars)
                closes = forward["Close"].astype(float)
                for label, n in HORIZON_DAYS.items():
                    if len(closes) > n:
                        outcome[f"return_filled_{label}"] = float((closes.iloc[n] - cost_basis) / cost_basis)
        records.append(outcome)

    enriched = cohort.copy().reset_index(drop=True)
    outcome_df = pd.DataFrame(records).reset_index(drop=True)
    return pd.concat([enriched, outcome_df], axis=1)
