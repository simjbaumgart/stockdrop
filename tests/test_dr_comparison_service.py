"""Tests for DRComparisonService — the dual-run challenger service.

Run with:
    python3 -m pytest tests/test_dr_comparison_service.py -v

All tests use an isolated temporary DB (same pattern as test_dr_comparison_db.py).
_run_challenger is called synchronously so no real threads are spawned.
"""
import json
import os
import sqlite3
import tempfile
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Fixture: isolated DB with a seeded decision_points row
# ---------------------------------------------------------------------------

CANNED_CLAUDE_RESULT = {
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
    "could_not_verify": ["recent earnings date"],
    "_claude_research_meta": {
        "source_urls": ["https://example.com/a", "https://example.com/b"],
        "search_count": 5,
        "latency_s": 12.3,
        "thinking": "some internal thoughts",
        "usage": {"in": 10000, "out": 2000},
    },
}

CANNED_CONTEXT = {"drop_percent": -6.5, "symbol": "AAPL"}


@pytest.fixture
def temp_db(monkeypatch):
    """Fresh isolated DB; yields (db_path, decision_id).

    The decision_points row has:
    - PM trading levels populated (for snapshot_pm_baseline)
    - deep_research_review_verdict already set (so _wait_for_gemini returns
      immediately without sleeping — safe for unit tests)
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    import app.database as db
    monkeypatch.setattr(db, "DB_NAME", path)
    db.init_db()

    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO decision_points (
            symbol, price_at_decision, drop_percent,
            recommendation, reasoning, status,
            conviction, entry_price_low, entry_price_high,
            stop_loss, take_profit_1, take_profit_2,
            sell_price_low, sell_price_high, ceiling_exit,
            risk_reward_ratio,
            deep_research_review_verdict, deep_research_action, deep_research_conviction,
            deep_research_score, deep_research_entry_low, deep_research_entry_high,
            deep_research_stop_loss, deep_research_tp1, deep_research_tp2,
            deep_research_rr_ratio
        ) VALUES (
            'AAPL', 180.0, -7.5,
            'BUY', 'Looks good', 'Pending DR Review',
            'HIGH', 175.0, 178.0,
            170.0, 195.0, 210.0,
            190.0, 205.0, 220.0,
            3.5,
            'CONFIRMED', 'BUY', 'HIGH',
            85, 174.0, 177.5,
            169.0, 194.0, 209.0,
            3.4
        )
        """,
    )
    decision_id = cur.lastrowid
    conn.commit()
    conn.close()

    yield path, decision_id

    os.unlink(path)


def _seed_pending_row(decision_id, db_path, monkeypatch):
    """Insert a PENDING dr_comparison row and return its id."""
    import app.database as db
    # DB_NAME is already monkeypatched
    from app.database import snapshot_pm_baseline, create_dr_comparison
    pm = snapshot_pm_baseline(decision_id)
    return create_dr_comparison(decision_id, "AAPL", "2026-05-29", pm)


# ---------------------------------------------------------------------------
# Test 1: happy path — _run_challenger ends with FINALIZED + cl_* populated
# ---------------------------------------------------------------------------

def test_run_challenger_happy_path(temp_db, monkeypatch):
    """_run_challenger should populate cl_* columns and finalize the row."""
    db_path, decision_id = temp_db

    import app.database as db
    # DB already patched by fixture

    comp_id = _seed_pending_row(decision_id, db_path, monkeypatch)
    assert comp_id > 0

    from app.services.dr_comparison_service import DRComparisonService

    svc = DRComparisonService()

    with mock.patch(
        "app.services.claude_deep_research_service.claude_deep_research_service.execute_deep_research",
        return_value=CANNED_CLAUDE_RESULT,
    ):
        # _wait_for_gemini uses DB_NAME directly; verdict is already set in the row
        # so it should return on the first poll. Pass tiny timeout/poll to be safe.
        orig_wait = svc._wait_for_gemini

        def fast_wait(decision_id, timeout_s=600, poll_s=15):
            return orig_wait(decision_id, timeout_s=5, poll_s=1)

        svc._wait_for_gemini = fast_wait
        svc._run_challenger(comp_id, decision_id, "AAPL", CANNED_CONTEXT)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM dr_comparison WHERE id = ?", (comp_id,))
    row = dict(cur.fetchone())
    conn.close()

    assert row["status"] == "FINALIZED", f"Expected FINALIZED, got {row['status']}"
    assert row["cl_review_verdict"] == "CONFIRMED"
    assert row["cl_action"] == "BUY"
    assert row["cl_conviction"] == "HIGH"
    assert row["cl_entry_low"] == pytest.approx(174.0)
    assert row["cl_source_count"] == 2
    assert row["cl_search_count"] == 5
    assert row["cl_latency_s"] == pytest.approx(12.3)
    # cost = compute_cost(claude-opus-4-8, 10000 in, 2000 out) + search_cost
    # = (10000/1e6)*5 + (2000/1e6)*25 + (5/1000)*10
    # = 0.05 + 0.05 + 0.05 = 0.15
    assert row["cl_cost_usd"] == pytest.approx(0.15, abs=1e-4)
    # gem_* filled from decision_points
    assert row["gem_review_verdict"] == "CONFIRMED"
    assert row["gem_action"] == "BUY"
    assert row["gem_entry_low"] == pytest.approx(174.0)


# ---------------------------------------------------------------------------
# Test 2: challenger failure is isolated — row marked FAILED, no exception raised
# ---------------------------------------------------------------------------

def test_run_challenger_failure_isolated(temp_db, monkeypatch):
    """If execute_deep_research raises, _run_challenger must NOT raise and
    must set the row status to FAILED."""
    db_path, decision_id = temp_db

    comp_id = _seed_pending_row(decision_id, db_path, monkeypatch)
    assert comp_id > 0

    from app.services.dr_comparison_service import DRComparisonService
    svc = DRComparisonService()

    with mock.patch(
        "app.services.claude_deep_research_service.claude_deep_research_service.execute_deep_research",
        side_effect=RuntimeError("Claude API exploded"),
    ):
        # Should not raise
        svc._run_challenger(comp_id, decision_id, "AAPL", CANNED_CONTEXT)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT status FROM dr_comparison WHERE id = ?", (comp_id,))
    row = cur.fetchone()
    conn.close()

    assert row["status"] == "FAILED"


# ---------------------------------------------------------------------------
# Test 3a: queue_research_task enqueues Gemini AND calls trigger when DR_DUAL_RUN=true
# ---------------------------------------------------------------------------

def test_queue_research_task_enqueues_gemini_and_triggers_challenger(monkeypatch):
    """With DR_DUAL_RUN=true, queue_research_task should:
    1. Put a task on the individual_queue (Gemini stays authoritative).
    2. Call dr_comparison_service.trigger once with the correct args.
    """
    monkeypatch.setenv("DR_DUAL_RUN", "true")

    from app.services.deep_research_service import deep_research_service

    # Clear any in-flight state so the dedup doesn't block us
    with deep_research_service._inflight_lock:
        deep_research_service._inflight.discard(("TSLA", deep_research_service._today_str()))

    queue_size_before = deep_research_service.individual_queue.qsize()

    with mock.patch(
        "app.services.dr_comparison_service.dr_comparison_service.trigger"
    ) as mock_trigger:
        result = deep_research_service.queue_research_task("TSLA", CANNED_CONTEXT, 42)

    assert result is True, "queue_research_task should return True when not deduped"
    assert deep_research_service.individual_queue.qsize() == queue_size_before + 1
    mock_trigger.assert_called_once_with(42, "TSLA", CANNED_CONTEXT)

    # Clean up inflight so other tests aren't polluted
    with deep_research_service._inflight_lock:
        deep_research_service._inflight.discard(("TSLA", deep_research_service._today_str()))


# ---------------------------------------------------------------------------
# Test 3b: trigger raising must NOT prevent queue_research_task from returning True
# ---------------------------------------------------------------------------

def test_queue_research_task_survives_trigger_exception(monkeypatch):
    """If dr_comparison_service.trigger raises, queue_research_task must still
    return True (live path unaffected)."""
    monkeypatch.setenv("DR_DUAL_RUN", "1")

    from app.services.deep_research_service import deep_research_service

    with deep_research_service._inflight_lock:
        deep_research_service._inflight.discard(("NVDA", deep_research_service._today_str()))

    with mock.patch(
        "app.services.dr_comparison_service.dr_comparison_service.trigger",
        side_effect=RuntimeError("trigger boom"),
    ):
        result = deep_research_service.queue_research_task("NVDA", CANNED_CONTEXT, 99)

    assert result is True

    with deep_research_service._inflight_lock:
        deep_research_service._inflight.discard(("NVDA", deep_research_service._today_str()))


# ---------------------------------------------------------------------------
# Test 3c: DR_DUAL_RUN not set — trigger is NOT called at all
# ---------------------------------------------------------------------------

def test_queue_research_task_no_trigger_when_dual_run_off(monkeypatch):
    """When DR_DUAL_RUN is absent, trigger must never be called."""
    monkeypatch.delenv("DR_DUAL_RUN", raising=False)

    from app.services.deep_research_service import deep_research_service

    with deep_research_service._inflight_lock:
        deep_research_service._inflight.discard(("MSFT", deep_research_service._today_str()))

    with mock.patch(
        "app.services.dr_comparison_service.dr_comparison_service.trigger"
    ) as mock_trigger:
        deep_research_service.queue_research_task("MSFT", CANNED_CONTEXT, 77)

    mock_trigger.assert_not_called()

    with deep_research_service._inflight_lock:
        deep_research_service._inflight.discard(("MSFT", deep_research_service._today_str()))


# ---------------------------------------------------------------------------
# Test 4a: _wait_for_gemini returns promptly when verdict already set
# ---------------------------------------------------------------------------

def test_wait_for_gemini_returns_when_verdict_set(temp_db, monkeypatch):
    """_wait_for_gemini should return quickly (no sleeping) when the verdict
    column is already populated."""
    db_path, decision_id = temp_db
    # verdict is already set in the temp_db fixture

    from app.services.dr_comparison_service import DRComparisonService
    svc = DRComparisonService()

    import time
    start = time.monotonic()
    # Use tiny timeout/poll to keep the test sub-second even if it polls once
    svc._wait_for_gemini(decision_id, timeout_s=5, poll_s=1)
    elapsed = time.monotonic() - start

    # Should return well under the 5s timeout (no sleeping needed since verdict is set)
    assert elapsed < 4.0, f"_wait_for_gemini took too long: {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Test 4b: _wait_for_gemini respects timeout when verdict never arrives
# ---------------------------------------------------------------------------

def test_wait_for_gemini_times_out(temp_db, monkeypatch):
    """_wait_for_gemini should give up and return after timeout_s when the
    verdict is never populated (does not raise)."""
    db_path, decision_id = temp_db

    # Blank out the verdict so it never becomes truthy
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE decision_points SET deep_research_review_verdict = NULL WHERE id = ?",
        (decision_id,),
    )
    conn.commit()
    conn.close()

    from app.services.dr_comparison_service import DRComparisonService
    svc = DRComparisonService()

    import time
    start = time.monotonic()
    # 2s timeout, 1s poll — should exit in ~2s
    svc._wait_for_gemini(decision_id, timeout_s=2, poll_s=1)
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"_wait_for_gemini ran too long: {elapsed:.2f}s"
    # Should not raise


# ---------------------------------------------------------------------------
# Test 5: execute_deep_research returning None marks row FAILED
# ---------------------------------------------------------------------------

def test_run_challenger_none_result_marks_failed(temp_db, monkeypatch):
    """If execute_deep_research returns None (not an exception), the row must
    be set to FAILED and _run_challenger must not raise."""
    db_path, decision_id = temp_db
    comp_id = _seed_pending_row(decision_id, db_path, monkeypatch)

    from app.services.dr_comparison_service import DRComparisonService
    svc = DRComparisonService()

    with mock.patch(
        "app.services.claude_deep_research_service.claude_deep_research_service.execute_deep_research",
        return_value=None,
    ):
        svc._run_challenger(comp_id, decision_id, "AAPL", CANNED_CONTEXT)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT status FROM dr_comparison WHERE id = ?", (comp_id,))
    row = cur.fetchone()
    conn.close()

    assert row[0] == "FAILED"
