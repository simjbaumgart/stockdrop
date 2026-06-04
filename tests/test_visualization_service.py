import pandas as pd
import pytest

from app.services.visualization_service import build_basket_curves


def _series(dates, values):
    return pd.Series(values, index=pd.to_datetime(dates))


def test_basket_curve_single_position_tracks_price_ratio():
    # Trading-day axis = SPY's index.
    axis = ["2026-04-10", "2026-04-11", "2026-04-12"]
    spy = _series(axis, [100.0, 100.0, 110.0])          # SPY +10% by day 3
    # One AAA position, entered day 1 at price_at_decision=50, price doubles by day 3.
    prices = {
        "SPY": spy,
        "AAA": _series(axis, [50.0, 60.0, 100.0]),
    }
    df = pd.DataFrame({
        "symbol": ["AAA"],
        "price_at_decision": [50.0],
        "date": pd.to_datetime(["2026-04-10"]),
        "council_intent": ["ENTER_NOW"],
    })

    out = build_basket_curves(df, prices, spy, "council_intent")

    curve = out["curves"]["ENTER_NOW"]
    # close/entry - 1, *100: 50/50-1=0, 60/50-1=20%, 100/50-1=100%
    assert curve["vals"] == pytest.approx([0.0, 20.0, 100.0])
    assert curve["final_n"] == 1
    # SPY normalized at chart start (day 1): 0%, 0%, +10%
    assert out["spy_vals"] == pytest.approx([0.0, 0.0, 10.0])


def test_basket_curve_position_excluded_before_entry_then_averaged():
    axis = ["2026-04-10", "2026-04-11", "2026-04-12"]
    spy = _series(axis, [100.0, 100.0, 100.0])
    prices = {
        "SPY": spy,
        "AAA": _series(axis, [50.0, 50.0, 50.0]),   # flat, entered day 1
        "BBB": _series(axis, [10.0, 10.0, 20.0]),   # entered day 3 only
    }
    df = pd.DataFrame({
        "symbol": ["AAA", "BBB"],
        "price_at_decision": [50.0, 10.0],
        "date": pd.to_datetime(["2026-04-10", "2026-04-12"]),
        "council_intent": ["ENTER_NOW", "ENTER_NOW"],
    })

    out = build_basket_curves(df, prices, spy, "council_intent")
    curve = out["curves"]["ENTER_NOW"]

    # Day1: only AAA (0%). Day2: only AAA (0%). Day3: AAA 0% and BBB +100% -> mean 50%.
    assert curve["vals"] == pytest.approx([0.0, 0.0, 50.0])
    assert curve["final_n"] == 2


def test_basket_curve_clips_extreme_position_return():
    axis = ["2026-04-10", "2026-04-11"]
    spy = _series(axis, [100.0, 100.0])
    prices = {
        "SPY": spy,
        "AAA": _series(axis, [1.0, 100.0]),  # +9900%, must clip to +300%
    }
    df = pd.DataFrame({
        "symbol": ["AAA"],
        "price_at_decision": [1.0],
        "date": pd.to_datetime(["2026-04-10"]),
        "council_intent": ["ENTER_NOW"],
    })

    out = build_basket_curves(df, prices, spy, "council_intent")
    assert out["curves"]["ENTER_NOW"]["vals"][-1] == pytest.approx(300.0)
