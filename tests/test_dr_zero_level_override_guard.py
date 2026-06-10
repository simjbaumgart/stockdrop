"""Tests for the zero-level guard in DR trading-level overrides.

Regression (GNRC/MOD 2026-06-10): both DR results went through the Gemini
Flash JSON repair, which returned 0.0 for entry/stop/TP fields.
_apply_trading_level_overrides only checked `val is not None`, so it
overwrote the PM's real levels with entry=0.0-0.0, stop 0.0, R/R 0.0.
A price of 0 is never a real level: <= 0 must be treated as missing, and
when no usable entry/stop survives, ALL level fields must be kept from
the PM (no override) with a warning.
"""
import sqlite3

from app.services.deep_research_service import DeepResearchService


ZEROED = {
    "action": "BUY",
    "entry_price_low": 0.0,
    "entry_price_high": 0.0,
    "stop_loss": 0.0,
    "take_profit_1": 0.0,
    "take_profit_2": 0.0,
    "upside_percent": 0.0,
    "downside_risk_percent": 0.0,
    "risk_reward_ratio": 0.0,
}

VALID = {
    "action": "BUY",
    "entry_price_low": 100.0,
    "entry_price_high": 105.0,
    "stop_loss": 92.0,
    "take_profit_1": 120.0,
    "risk_reward_ratio": 2.5,
}


def test_zeroed_levels_all_dropped():
    levels, dropped = DeepResearchService._clean_level_overrides(ZEROED)
    assert levels == {}
    assert dropped  # the rejected zeros are reported for the warning log


def test_valid_levels_pass_through():
    levels, dropped = DeepResearchService._clean_level_overrides(VALID)
    assert levels["entry_price_low"] == 100.0
    assert levels["entry_price_high"] == 105.0
    assert levels["stop_loss"] == 92.0
    assert levels["take_profit_1"] == 120.0
    assert levels["risk_reward_ratio"] == 2.5
    assert dropped == []


def test_single_zero_tp_dropped_rest_kept():
    result = dict(VALID, take_profit_1=0.0)
    levels, dropped = DeepResearchService._clean_level_overrides(result)
    assert "take_profit_1" not in levels
    assert levels["entry_price_low"] == 100.0
    assert any("take_profit_1" in d for d in dropped)


def test_zero_entry_and_stop_gate_out_valid_tp():
    """If no usable entry/stop survives, even a plausible TP must not be
    written — partial overrides would mix DR's TP with the PM's entry."""
    result = {
        "action": "BUY",
        "entry_price_low": 0.0,
        "entry_price_high": 0.0,
        "stop_loss": 0.0,
        "take_profit_1": 120.0,
        "sell_price_high": 130.0,
    }
    levels, dropped = DeepResearchService._clean_level_overrides(result)
    assert levels == {}
    assert dropped


def test_absent_levels_are_not_an_error():
    """AVOID results legitimately carry no levels: nothing to write, nothing
    to warn about."""
    levels, dropped = DeepResearchService._clean_level_overrides({"action": "AVOID"})
    assert levels == {}
    assert dropped == []


def test_non_numeric_level_dropped():
    result = dict(VALID, stop_loss="N/A")
    levels, dropped = DeepResearchService._clean_level_overrides(result)
    assert "stop_loss" not in levels
    assert any("stop_loss" in d for d in dropped)


def test_exit_trigger_kept_with_valid_levels():
    result = dict(VALID, exit_trigger="close below 90")
    levels, _ = DeepResearchService._clean_level_overrides(result)
    assert levels["exit_trigger"] == "close below 90"


def test_override_keeps_pm_levels_on_zeroed_result(tmp_path, monkeypatch):
    """End-to-end: a zeroed DR result must leave the PM's DB levels intact
    (metadata like conviction may still be updated)."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE decision_points (
            id INTEGER PRIMARY KEY,
            entry_price_low REAL, entry_price_high REAL, stop_loss REAL,
            take_profit_1 REAL, take_profit_2 REAL,
            upside_percent REAL, downside_risk_percent REAL,
            risk_reward_ratio REAL,
            sell_price_low REAL, sell_price_high REAL, ceiling_exit REAL,
            exit_trigger TEXT, conviction TEXT, drop_type TEXT,
            entry_trigger TEXT, reassess_in_days INTEGER
        )"""
    )
    conn.execute(
        "INSERT INTO decision_points (id, entry_price_low, entry_price_high, "
        "stop_loss, risk_reward_ratio, conviction) VALUES (1, 100.0, 105.0, 92.0, 2.5, 'MODERATE')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DB_PATH", str(db))
    svc = DeepResearchService.__new__(DeepResearchService)  # bypass __init__/network
    svc._apply_trading_level_overrides(1, "MOD", dict(ZEROED, conviction="HIGH"))

    row = sqlite3.connect(db).execute(
        "SELECT entry_price_low, entry_price_high, stop_loss, risk_reward_ratio, conviction "
        "FROM decision_points WHERE id = 1"
    ).fetchone()
    assert row == (100.0, 105.0, 92.0, 2.5, "HIGH")
