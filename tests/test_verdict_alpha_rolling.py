"""Tier-3 console view: rolling per-verdict forward returns (never injected)."""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


def _row(ts, rec, dr, r1, r4):
    return {"timestamp": ts, "recommendation": rec, "deep_research_verdict": dr,
            "ret_1w": r1, "ret_2w": None, "ret_4w": r4, "ret_8w": None}


def test_compute_view_windows_and_stats():
    from scripts.analysis.verdict_alpha_rolling import compute_view
    today = datetime.date(2026, 7, 2)
    rows = [
        _row("2026-06-20 10:00:00", "BUY", "BUY_NOW", 0.01, 0.10),   # 12d ago
        _row("2026-06-10 10:00:00", "BUY", None, -0.02, -0.04),      # 22d ago
        _row("2026-03-01 10:00:00", "BUY", "BUY_NOW", 0.05, 0.20),   # outside 90d
        _row("2026-06-25 10:00:00", "AVOID", "HOLD_OFF", None, 0.105),
    ]
    view = compute_view(rows, today, windows=(30, 90))

    buy30 = view[30]["by_pm_verdict"]["BUY"]
    assert buy30["4w"]["n"] == 2
    assert buy30["4w"]["mean"] == pytest.approx((0.10 - 0.04) / 2)
    assert buy30["4w"]["win_rate"] == pytest.approx(0.5)
    assert buy30["1w"]["n"] == 2

    avoid30 = view[30]["by_pm_verdict"]["AVOID"]
    assert avoid30["4w"]["n"] == 1
    assert avoid30["1w"]["n"] == 0          # None returns are excluded per-horizon

    assert view[90]["by_pm_verdict"]["BUY"]["4w"]["n"] == 2  # March row excluded
    assert view[30]["by_dr_verdict"]["BUY_NOW"]["4w"]["n"] == 1


def test_render_view_prints_tables():
    from scripts.analysis.verdict_alpha_rolling import compute_view, render_view
    today = datetime.date(2026, 7, 2)
    rows = [_row("2026-06-20 10:00:00", "BUY", "BUY_NOW", 0.01, 0.10)]
    out = render_view(compute_view(rows, today, windows=(30,)))
    assert "trailing 30d" in out
    assert "BUY" in out and "4w" in out
