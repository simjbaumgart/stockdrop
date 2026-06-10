"""Tests for same-day horizon handling in the trade report.

Regression (2026-06-10): same-day decisions showed populated Price +7d /
Performance (TLN -1.00%, SMMT -1.01%) with index columns at +0.00%, while
other same-day rows showed "-". The pending-window lookup fell back to the
latest cached price, but only when the symbol happened to be in the
1-hour-old cache. Same-day rows must uniformly resolve to None (rendered
"-") — with zero elapsed days a "latest" fallback is just the intraday move.
"""
from datetime import datetime, timedelta

from scripts.core.generate_trade_report import _resolve_horizon_price


NOW = datetime(2026, 6, 10, 16, 0)


def test_same_day_returns_none_even_when_cached():
    decision_dt = datetime(2026, 6, 10, 9, 30)
    price = _resolve_horizon_price(
        decision_dt, decision_dt + timedelta(days=7), NOW,
        exact_lookup=lambda dt: 50.0, latest_price=lambda: 42.0,
    )
    assert price is None


def test_pending_window_uses_latest_price():
    decision_dt = NOW - timedelta(days=3)
    price = _resolve_horizon_price(
        decision_dt, decision_dt + timedelta(days=7), NOW,
        exact_lookup=lambda dt: None, latest_price=lambda: 42.0,
    )
    assert price == 42.0


def test_completed_window_uses_exact_price():
    decision_dt = NOW - timedelta(days=10)
    price = _resolve_horizon_price(
        decision_dt, decision_dt + timedelta(days=7), NOW,
        exact_lookup=lambda dt: 50.0, latest_price=lambda: 42.0,
    )
    assert price == 50.0


def test_completed_window_falls_back_to_latest_when_exact_missing():
    decision_dt = NOW - timedelta(days=10)
    price = _resolve_horizon_price(
        decision_dt, decision_dt + timedelta(days=7), NOW,
        exact_lookup=lambda dt: None, latest_price=lambda: 42.0,
    )
    assert price == 42.0
