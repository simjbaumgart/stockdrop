import numpy as np
import pandas as pd
import pytest

from app.services.analytics.outcomes import compute_outcome, enrich_outcomes


def make_bars(start, n_days, prices):
    idx = pd.bdate_range(start=start, periods=n_days)
    return pd.DataFrame(
        {"Open": prices, "High": prices, "Low": prices, "Close": prices},
        index=idx,
    )


def test_compute_outcome_basic_returns():
    decision_date = pd.Timestamp("2026-02-02")
    closes = [100 + 2 * i for i in range(45)]  # 100,102,...,188
    bars = make_bars(decision_date, len(closes), closes)
    out = compute_outcome(
        decision_price=100.0,
        decision_date=decision_date,
        bars=bars,
        pre_drop_price=110.0,
    )
    assert out["return_1w"] == pytest.approx((closes[5] - 100) / 100, abs=1e-3)
    assert out["return_2w"] == pytest.approx((closes[10] - 100) / 100, abs=1e-3)
    assert out["return_4w"] == pytest.approx((closes[20] - 100) / 100, abs=1e-3)
    assert out["recovered"] is True
    assert out["days_to_recover"] == 5


def test_compute_outcome_handles_drawdown():
    decision_date = pd.Timestamp("2026-02-02")
    closes = [100, 95, 90, 85, 90, 95, 100, 105]
    bars = make_bars(decision_date, len(closes), closes)
    out = compute_outcome(
        decision_price=100.0,
        decision_date=decision_date,
        bars=bars,
        pre_drop_price=110.0,
    )
    assert out["max_drawdown_4w"] < 0
    assert out["max_drawdown_4w"] == pytest.approx(-0.15, abs=1e-3)
    assert out["recovered"] is False


def test_compute_outcome_insufficient_bars_returns_nan():
    decision_date = pd.Timestamp("2026-02-02")
    bars = make_bars(decision_date, 3, [100, 101, 102])
    out = compute_outcome(
        decision_price=100.0,
        decision_date=decision_date,
        bars=bars,
        pre_drop_price=None,
    )
    assert np.isnan(out["return_1w"])
    assert np.isnan(out["return_4w"])


def test_enrich_outcomes_with_cohort():
    decision_date = pd.Timestamp("2026-02-02")
    cohort = pd.DataFrame([
        {
            "id": 1,
            "symbol": "TEST",
            "price_at_decision": 100.0,
            "decision_date": decision_date,
            "drop_percent": -10.0,
            "intent": "ENTER_NOW",
            "entry_price_low": None,
            "entry_price_high": None,
        }
    ])
    closes = list(range(100, 145))
    bars = {"TEST": make_bars(decision_date, len(closes), closes)}
    enriched = enrich_outcomes(cohort, bars)
    assert "return_1w" in enriched.columns
    assert "max_roi_4w" in enriched.columns
    assert enriched.iloc[0]["return_1w"] == pytest.approx(0.05, abs=1e-3)
