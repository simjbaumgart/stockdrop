import numpy as np
import pandas as pd
import pytest

from app.services.analytics.aggregations import (
    equity_curve,
    winrate_by,
    winrate_by_bucket,
)


def make_df():
    return pd.DataFrame([
        {"id": 1, "intent": "ENTER_NOW", "drop_percent": -6, "return_4w": 0.10,
         "decision_date": pd.Timestamp("2026-02-02")},
        {"id": 2, "intent": "ENTER_NOW", "drop_percent": -7, "return_4w": -0.02,
         "decision_date": pd.Timestamp("2026-02-05")},
        {"id": 3, "intent": "ENTER_NOW", "drop_percent": -10, "return_4w": 0.20,
         "decision_date": pd.Timestamp("2026-02-08")},
        {"id": 4, "intent": "AVOID", "drop_percent": -8, "return_4w": -0.05,
         "decision_date": pd.Timestamp("2026-02-12")},
        {"id": 5, "intent": "AVOID", "drop_percent": -12, "return_4w": np.nan,
         "decision_date": pd.Timestamp("2026-04-30")},
    ])


def test_winrate_by_intent_4w():
    agg = winrate_by(make_df(), group_col="intent", horizon="4w")
    enter = agg.loc[agg["intent"] == "ENTER_NOW"].iloc[0]
    assert enter["count"] == 3
    assert enter["win_rate"] == pytest.approx(2 / 3)
    assert enter["avg_return"] == pytest.approx((0.10 - 0.02 + 0.20) / 3)


def test_winrate_by_intent_excludes_nan():
    agg = winrate_by(make_df(), group_col="intent", horizon="4w")
    avoid = agg.loc[agg["intent"] == "AVOID"].iloc[0]
    assert avoid["count"] == 1


def test_winrate_by_bucket_drop_percent():
    bins = [-100, -10, -7, 0]
    agg = winrate_by_bucket(make_df(), value_col="drop_percent", bins=bins, horizon="4w")
    assert "bucket" in agg.columns
    assert (agg["count"] >= 1).all()


def test_equity_curve_cumulative():
    df = make_df()
    df = df[df["intent"] == "ENTER_NOW"].copy()
    curve = equity_curve(df, horizon="4w")
    assert "equity" in curve.columns
    assert len(curve) == 3
    assert curve["equity"].iloc[-1] == pytest.approx(1.10 * 0.98 * 1.20, rel=1e-3)
