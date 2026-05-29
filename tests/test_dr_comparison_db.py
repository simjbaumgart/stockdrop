"""Tests for the dr_comparison table and its helper functions.

Run with:
    python3 -m pytest tests/test_dr_comparison_db.py -v
"""
import json
import os
import sqlite3
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Fixture: isolated DB
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(monkeypatch):
    """Fresh sqlite DB wired to app.database via monkeypatch.setattr.

    Yields: (path: str, decision_id: int)
    The decision_id belongs to a row in decision_points that has PM trading
    levels pre-populated so snapshot and finalize tests work without hitting
    a live DB.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    import app.database as db
    monkeypatch.setattr(db, "DB_NAME", path)
    db.init_db()

    conn = sqlite3.connect(path)
    cur = conn.cursor()
    # Insert a decision_points row with PM trading levels set
    cur.execute(
        """
        INSERT INTO decision_points (
            symbol, price_at_decision, drop_percent,
            recommendation, reasoning, status,
            conviction, entry_price_low, entry_price_high,
            stop_loss, take_profit_1, take_profit_2,
            sell_price_low, sell_price_high, ceiling_exit,
            risk_reward_ratio
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "AAPL", 180.0, -7.5,
            "BUY", "Looks good", "Pending DR Review",
            "HIGH", 175.0, 178.0,
            170.0, 195.0, 210.0,
            190.0, 205.0, 220.0,
            3.5,
        ),
    )
    decision_id = cur.lastrowid
    conn.commit()
    conn.close()
    yield path, decision_id
    os.unlink(path)


# ---------------------------------------------------------------------------
# Test 1: create_dr_comparison
# ---------------------------------------------------------------------------

def test_create_dr_comparison_inserts_pending_row(temp_db):
    """create_dr_comparison inserts PENDING row with pm_* fields; returns an id."""
    _, decision_id = temp_db

    from app.database import create_dr_comparison, snapshot_pm_baseline

    pm_baseline = snapshot_pm_baseline(decision_id)
    comp_id = create_dr_comparison(decision_id, "AAPL", "2026-05-29", pm_baseline)

    assert isinstance(comp_id, int), "must return an integer id"
    assert comp_id > 0

    # Verify the row is readable and has correct values
    path, _ = temp_db
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM dr_comparison WHERE id = ?", (comp_id,))
    row = dict(cur.fetchone())
    conn.close()

    assert row["decision_id"] == decision_id
    assert row["symbol"] == "AAPL"
    assert row["run_date"] == "2026-05-29"
    assert row["status"] == "PENDING"
    assert row["pm_recommendation"] == "BUY"
    assert row["pm_conviction"] == "HIGH"
    assert row["pm_entry_low"] == pytest.approx(175.0)
    assert row["pm_entry_high"] == pytest.approx(178.0)
    assert row["pm_stop_loss"] == pytest.approx(170.0)
    assert row["pm_tp1"] == pytest.approx(195.0)
    assert row["pm_tp2"] == pytest.approx(210.0)
    assert row["pm_sell_low"] == pytest.approx(190.0)
    assert row["pm_sell_high"] == pytest.approx(205.0)
    assert row["pm_ceiling_exit"] == pytest.approx(220.0)
    assert row["pm_rr_ratio"] == pytest.approx(3.5)


# ---------------------------------------------------------------------------
# Test 2: update_dr_comparison_claude
# ---------------------------------------------------------------------------

def test_update_dr_comparison_claude_fills_cl_columns(temp_db):
    """update_dr_comparison_claude fills cl_* columns, sets CLAUDE_DONE,
    stores could_not_verify list as JSON."""
    _, decision_id = temp_db

    from app.database import create_dr_comparison, update_dr_comparison_claude, snapshot_pm_baseline

    pm_baseline = snapshot_pm_baseline(decision_id)
    comp_id = create_dr_comparison(decision_id, "AAPL", "2026-05-29", pm_baseline)

    claude_result = {
        "review_verdict": "CONFIRMED",
        "action": "BUY",
        "conviction": "HIGH",
        "entry_price_low": 174.0,
        "entry_price_high": 177.5,
        "stop_loss": 169.0,
        "take_profit_1": 194.0,
        "take_profit_2": 209.0,
        "sell_price_low": 189.0,
        "sell_price_high": 204.0,
        "ceiling_exit": 219.0,
        "risk_reward_ratio": 3.4,
        "entry_trigger": "Bounce off 50d MA",
        "exit_trigger": "Break above 52-week high",
        "reason": "Strong fundamentals, oversold technically",
        "knife_catch_warning": "LOW",
        "could_not_verify": ["recent earnings date", "guidance revision"],
    }
    meta = {
        "search_count": 12,
        "source_count": 8,
        "cost_usd": 0.043,
        "latency_s": 18.7,
    }

    update_dr_comparison_claude(comp_id, claude_result, meta)

    path, _ = temp_db
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM dr_comparison WHERE id = ?", (comp_id,))
    row = dict(cur.fetchone())
    conn.close()

    assert row["status"] == "CLAUDE_DONE"
    assert row["cl_review_verdict"] == "CONFIRMED"
    assert row["cl_action"] == "BUY"
    assert row["cl_conviction"] == "HIGH"
    assert row["cl_entry_low"] == pytest.approx(174.0)
    assert row["cl_entry_high"] == pytest.approx(177.5)
    assert row["cl_stop_loss"] == pytest.approx(169.0)
    assert row["cl_tp1"] == pytest.approx(194.0)
    assert row["cl_tp2"] == pytest.approx(209.0)
    assert row["cl_sell_low"] == pytest.approx(189.0)
    assert row["cl_sell_high"] == pytest.approx(204.0)
    assert row["cl_ceiling_exit"] == pytest.approx(219.0)
    assert row["cl_rr_ratio"] == pytest.approx(3.4)
    assert row["cl_entry_trigger"] == "Bounce off 50d MA"
    assert row["cl_exit_trigger"] == "Break above 52-week high"
    assert row["cl_knife_catch"] == "LOW"
    assert row["cl_search_count"] == 12
    assert row["cl_source_count"] == 8
    assert row["cl_cost_usd"] == pytest.approx(0.043)
    assert row["cl_latency_s"] == pytest.approx(18.7)

    # could_not_verify stored as JSON list
    cnv = json.loads(row["cl_could_not_verify"])
    assert cnv == ["recent earnings date", "guidance revision"]

    # cl_result_json should contain the full dict
    result_json = json.loads(row["cl_result_json"])
    assert result_json["action"] == "BUY"


# ---------------------------------------------------------------------------
# Test 3: finalize_dr_comparison
# ---------------------------------------------------------------------------

def _seed_gemini_levels(path: str, decision_id: int, levels: dict) -> None:
    """Directly write deep_research_* columns into decision_points for test setup."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE decision_points SET
            deep_research_review_verdict = :review_verdict,
            deep_research_action = :action,
            deep_research_conviction = :conviction,
            deep_research_score = :score,
            deep_research_entry_low = :entry_low,
            deep_research_entry_high = :entry_high,
            deep_research_stop_loss = :stop_loss,
            deep_research_tp1 = :tp1,
            deep_research_tp2 = :tp2,
            deep_research_rr_ratio = :rr_ratio,
            deep_research_entry_trigger = :entry_trigger,
            deep_research_exit_trigger = :exit_trigger,
            deep_research_reason = :reason,
            deep_research_sell_price_low = :sell_low,
            deep_research_sell_price_high = :sell_high,
            deep_research_ceiling_exit = :ceiling_exit
        WHERE id = :decision_id
        """,
        {**levels, "decision_id": decision_id},
    )
    conn.commit()
    conn.close()


def test_finalize_dr_comparison_anchored_when_pm_null(temp_db):
    """anchored=1 when pm levels are NULL (baseline not captured in time)."""
    path, decision_id = temp_db

    from app.database import create_dr_comparison, finalize_dr_comparison

    # Create with empty pm_baseline (simulating missing snapshot)
    comp_id = create_dr_comparison(decision_id, "AAPL", "2026-05-29", {})

    gem_levels = {
        "review_verdict": "CONFIRMED", "action": "BUY", "conviction": "HIGH",
        "score": 82,
        "entry_low": 175.0, "entry_high": 178.0, "stop_loss": 170.0,
        "tp1": 195.0, "tp2": 210.0, "rr_ratio": 3.5,
        "entry_trigger": "Breakout", "exit_trigger": "Stop hit",
        "reason": "Strong buy",
        "sell_low": 190.0, "sell_high": 205.0, "ceiling_exit": 220.0,
    }
    _seed_gemini_levels(path, decision_id, gem_levels)

    finalize_dr_comparison(comp_id)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM dr_comparison WHERE id = ?", (comp_id,))
    row = dict(cur.fetchone())
    conn.close()

    assert row["status"] == "FINALIZED"
    assert row["anchored"] == 1, "should be anchored when pm_entry_low is NULL"
    # Gemini values should still be populated
    assert row["gem_review_verdict"] == "CONFIRMED"
    assert row["gem_entry_low"] == pytest.approx(175.0)
    assert row["gem_score"] == 82


def test_finalize_dr_comparison_anchored_when_pm_equals_gem(temp_db):
    """anchored=1 when pm levels are identical to gem levels (snapshot too late)."""
    path, decision_id = temp_db

    from app.database import create_dr_comparison, finalize_dr_comparison

    # pm levels already match what Gemini will produce
    pm_baseline = {
        "pm_recommendation": "BUY",
        "pm_conviction": "HIGH",
        "pm_entry_low": 175.0,
        "pm_entry_high": 178.0,
        "pm_stop_loss": 170.0,
        "pm_tp1": 195.0,
        "pm_tp2": 210.0,
        "pm_sell_low": 190.0,
        "pm_sell_high": 205.0,
        "pm_ceiling_exit": 220.0,
        "pm_rr_ratio": 3.5,
    }
    comp_id = create_dr_comparison(decision_id, "AAPL", "2026-05-29", pm_baseline)

    # Gem levels identical to pm
    gem_levels = {
        "review_verdict": "CONFIRMED", "action": "BUY", "conviction": "HIGH",
        "score": 82,
        "entry_low": 175.0, "entry_high": 178.0, "stop_loss": 170.0,
        "tp1": 195.0, "tp2": 210.0, "rr_ratio": 3.5,
        "entry_trigger": "Breakout", "exit_trigger": "Stop hit",
        "reason": "Confirmed",
        "sell_low": 190.0, "sell_high": 205.0, "ceiling_exit": 220.0,
    }
    _seed_gemini_levels(path, decision_id, gem_levels)

    finalize_dr_comparison(comp_id)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM dr_comparison WHERE id = ?", (comp_id,))
    row = dict(cur.fetchone())
    conn.close()

    assert row["status"] == "FINALIZED"
    assert row["anchored"] == 1, "should be anchored when pm levels == gem levels"


def test_finalize_dr_comparison_not_anchored_when_levels_differ(temp_db):
    """anchored=0 when pm levels genuinely differ from gem levels."""
    path, decision_id = temp_db

    from app.database import create_dr_comparison, finalize_dr_comparison

    pm_baseline = {
        "pm_recommendation": "BUY",
        "pm_conviction": "HIGH",
        "pm_entry_low": 175.0,
        "pm_entry_high": 178.0,
        "pm_stop_loss": 170.0,
        "pm_tp1": 195.0,
        "pm_tp2": 210.0,
        "pm_sell_low": 190.0,
        "pm_sell_high": 205.0,
        "pm_ceiling_exit": 220.0,
        "pm_rr_ratio": 3.5,
    }
    comp_id = create_dr_comparison(decision_id, "AAPL", "2026-05-29", pm_baseline)

    # Gem overrides with different levels
    gem_levels = {
        "review_verdict": "OVERRIDE", "action": "BUY_LIMIT", "conviction": "MEDIUM",
        "score": 70,
        "entry_low": 168.0, "entry_high": 172.0, "stop_loss": 162.0,
        "tp1": 188.0, "tp2": 200.0, "rr_ratio": 2.8,
        "entry_trigger": "Dip to support", "exit_trigger": "Close below 160",
        "reason": "Adjusted for macro risk",
        "sell_low": 185.0, "sell_high": 198.0, "ceiling_exit": 210.0,
    }
    _seed_gemini_levels(path, decision_id, gem_levels)

    finalize_dr_comparison(comp_id)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM dr_comparison WHERE id = ?", (comp_id,))
    row = dict(cur.fetchone())
    conn.close()

    assert row["status"] == "FINALIZED"
    assert row["anchored"] == 0, "should NOT be anchored when pm and gem levels differ"
    assert row["gem_entry_low"] == pytest.approx(168.0)
    assert row["gem_action"] == "BUY_LIMIT"


# ---------------------------------------------------------------------------
# Test 4: snapshot_pm_baseline
# ---------------------------------------------------------------------------

def test_snapshot_pm_baseline_returns_correct_mapping(temp_db):
    """snapshot_pm_baseline reads decision_points and maps to pm_* keys."""
    _, decision_id = temp_db

    from app.database import snapshot_pm_baseline

    baseline = snapshot_pm_baseline(decision_id)

    assert isinstance(baseline, dict)
    assert baseline["pm_recommendation"] == "BUY"
    assert baseline["pm_conviction"] == "HIGH"
    assert baseline["pm_entry_low"] == pytest.approx(175.0)
    assert baseline["pm_entry_high"] == pytest.approx(178.0)
    assert baseline["pm_stop_loss"] == pytest.approx(170.0)
    assert baseline["pm_tp1"] == pytest.approx(195.0)
    assert baseline["pm_tp2"] == pytest.approx(210.0)
    assert baseline["pm_sell_low"] == pytest.approx(190.0)
    assert baseline["pm_sell_high"] == pytest.approx(205.0)
    assert baseline["pm_ceiling_exit"] == pytest.approx(220.0)
    assert baseline["pm_rr_ratio"] == pytest.approx(3.5)


def test_snapshot_pm_baseline_missing_id_returns_empty(temp_db):
    """snapshot_pm_baseline returns {} for a non-existent decision_id."""
    from app.database import snapshot_pm_baseline

    result = snapshot_pm_baseline(99999)
    assert result == {}
