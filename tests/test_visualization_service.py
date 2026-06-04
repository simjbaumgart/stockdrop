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


def test_basket_curve_skips_nan_entry_price():
    # A NaN price_at_decision must be silently excluded, not poison the basket.
    axis = ["2026-04-10", "2026-04-11"]
    spy = _series(axis, [100.0, 100.0])
    prices = {
        "SPY": spy,
        "AAA": _series(axis, [50.0, 60.0]),   # valid, +20%
        "BBB": _series(axis, [10.0, 20.0]),   # would be +100% but its entry price is NaN
    }
    df = pd.DataFrame({
        "symbol": ["AAA", "BBB"],
        "price_at_decision": [50.0, float("nan")],
        "date": pd.to_datetime(["2026-04-10", "2026-04-10"]),
        "council_intent": ["ENTER_NOW", "ENTER_NOW"],
    })

    out = build_basket_curves(df, prices, spy, "council_intent")
    curve = out["curves"]["ENTER_NOW"]
    # Only AAA counts: day1 0%, day2 +20%. BBB excluded entirely.
    assert curve["vals"] == pytest.approx([0.0, 20.0])
    assert curve["final_n"] == 1


def test_render_basket_chart_runs_on_payload(capsys):
    from app.services.visualization_service import render_basket_chart

    payload = {
        "curves": {
            "ENTER_NOW": {
                "dates": pd.to_datetime(["2026-04-10", "2026-04-11"]).tolist(),
                "vals": [0.0, 5.0],
                "final_n": 2,
            }
        },
        "spy_dates": pd.to_datetime(["2026-04-10", "2026-04-11"]).tolist(),
        "spy_vals": [0.0, 1.0],
    }
    render_basket_chart("Test chart", payload)
    out = capsys.readouterr().out
    assert "Test chart" in out  # plotext renders the title into the terminal output


def test_render_basket_chart_handles_empty(capsys):
    from app.services.visualization_service import render_basket_chart

    render_basket_chart("Empty chart", {"curves": {}, "spy_dates": [], "spy_vals": []})
    out = capsys.readouterr().out
    assert "no data" in out.lower()


def test_parse_since_absolute_date():
    from app.services.visualization_service import parse_since

    dt = parse_since("2026-04-09")
    assert (dt.year, dt.month, dt.day) == (2026, 4, 9)


def test_parse_since_relative_weeks_and_days():
    from datetime import datetime

    from app.services.visualization_service import parse_since

    now = datetime.now()
    four_weeks = parse_since("4w")
    assert 27 <= (now - four_weeks).days <= 29  # ~28, allow clock drift

    thirty_days = parse_since("30d")
    assert 29 <= (now - thirty_days).days <= 31

    # A space before the unit is allowed.
    assert 6 <= (now - parse_since("1 w")).days <= 8


def test_parse_since_rejects_garbage():
    from app.services.visualization_service import parse_since

    with pytest.raises(ValueError):
        parse_since("banana")
