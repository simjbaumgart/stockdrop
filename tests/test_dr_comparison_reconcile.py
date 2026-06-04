"""Dual-run reconciliation: don't finalize Claude-only as a complete head-to-head.

Regression: 2026-05 benchmark batch. Gemini DR is serialized behind a single
worker (+60s cooldown); when a queue forms, _wait_for_gemini's 600s window
expired and _run_challenger called finalize_dr_comparison ANYWAY — stamping a
FINALIZED row with NULL gem_* columns. Four of five comparisons that run were
Claude-only but indistinguishable from real comparisons.

Fix contract:
  - _wait_for_gemini returns True iff Gemini's verdict landed, else False.
  - On False, the row is left PENDING_GEMINI (not FINALIZED).
  - reconcile_pending_gemini() later finalizes rows whose Gemini verdict has
    since landed, and ages truly-dead rows out to a terminal GEMINI_TIMEOUT.
"""
import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def temp_db(monkeypatch):
    """Fresh isolated DB with one decision_points row (no Gemini verdict yet)."""
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
            entry_price_low, entry_price_high, stop_loss
        ) VALUES ('TEST', 100.0, -6.0, 'BUY', 'x', 'Pending DR Review',
                  90.0, 95.0, 85.0)
        """,
    )
    decision_id = cur.lastrowid
    conn.commit()
    conn.close()
    yield path, decision_id
    os.unlink(path)


def _pending_gemini_row(decision_id, symbol="TEST"):
    from app.database import snapshot_pm_baseline, create_dr_comparison, set_dr_comparison_status
    pm = snapshot_pm_baseline(decision_id)
    cid = create_dr_comparison(decision_id, symbol, "2026-06-04", pm)
    set_dr_comparison_status(cid, "PENDING_GEMINI")
    return cid


def _set_gemini_verdict(path, decision_id):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        "UPDATE decision_points SET deep_research_review_verdict='CONFIRMED', "
        "deep_research_action='BUY', deep_research_entry_low=88.0, "
        "deep_research_entry_high=93.0, deep_research_stop_loss=83.0 WHERE id=?",
        (decision_id,),
    )
    conn.commit()
    conn.close()


def _status(path, cid):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("SELECT status FROM dr_comparison WHERE id=?", (cid,))
    row = cur.fetchone()
    conn.close()
    return row[0]


def _svc():
    from app.services.dr_comparison_service import DRComparisonService
    return DRComparisonService()


# --- _wait_for_gemini return-value contract -------------------------------

def test_wait_for_gemini_true_when_present(temp_db):
    path, decision_id = temp_db
    _set_gemini_verdict(path, decision_id)
    assert _svc()._wait_for_gemini(decision_id, timeout_s=5, poll_s=1) is True


def test_wait_for_gemini_false_on_timeout(temp_db):
    path, decision_id = temp_db
    # No Gemini verdict set -> times out quickly and reports False.
    assert _svc()._wait_for_gemini(decision_id, timeout_s=1, poll_s=1) is False


# --- reconcile_pending_gemini --------------------------------------------

def test_reconcile_finalizes_when_gemini_arrives(temp_db):
    path, decision_id = temp_db
    cid = _pending_gemini_row(decision_id)
    _set_gemini_verdict(path, decision_id)

    stats = _svc().reconcile_pending_gemini()

    assert _status(path, cid) == "FINALIZED"
    assert stats["finalized"] >= 1


def test_reconcile_leaves_recent_row_pending(temp_db):
    path, decision_id = temp_db
    cid = _pending_gemini_row(decision_id)  # no verdict, created just now

    stats = _svc().reconcile_pending_gemini(max_age_s=3600)

    assert _status(path, cid) == "PENDING_GEMINI"
    assert stats["pending"] >= 1


def test_reconcile_times_out_dead_old_rows(temp_db):
    path, decision_id = temp_db
    cid = _pending_gemini_row(decision_id)  # no verdict
    # Backdate so it exceeds the max age.
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        "UPDATE dr_comparison SET created_at = datetime('now','-1 day') WHERE id=?",
        (cid,),
    )
    conn.commit()
    conn.close()

    stats = _svc().reconcile_pending_gemini(max_age_s=3600)

    assert _status(path, cid) == "GEMINI_TIMEOUT"
    assert stats["timed_out"] >= 1


# --- _run_challenger no longer finalizes when Gemini is absent ------------

CANNED_CLAUDE_RESULT = {
    "review_verdict": "CONFIRMED",
    "action": "BUY",
    "conviction": "HIGH",
    "entry_price_low": 88.0,
    "entry_price_high": 93.0,
    "stop_loss": 83.0,
    "_claude_research_meta": {"source_urls": [], "search_count": 0, "latency_s": 1.0,
                              "usage": {"in": 100, "out": 50}},
}


def test_run_challenger_marks_pending_when_gemini_absent(temp_db, monkeypatch):
    from app.database import snapshot_pm_baseline, create_dr_comparison
    path, decision_id = temp_db
    pm = snapshot_pm_baseline(decision_id)
    cid = create_dr_comparison(decision_id, "TEST", "2026-06-04", pm)

    svc = _svc()
    svc._wait_for_gemini = lambda *a, **k: False  # Gemini never lands

    with __import__("unittest").mock.patch(
        "app.services.claude_deep_research_service.claude_deep_research_service.execute_deep_research",
        return_value=CANNED_CLAUDE_RESULT,
    ):
        svc._run_challenger(cid, decision_id, "TEST", {})

    # Claude side stored, but the row must NOT be FINALIZED (no Gemini half).
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM dr_comparison WHERE id=?", (cid,))
    row = dict(cur.fetchone())
    conn.close()

    assert row["status"] == "PENDING_GEMINI"
    assert row["cl_review_verdict"] == "CONFIRMED"
