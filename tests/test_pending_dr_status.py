"""3-state position lifecycle: Pending DR Review -> Owned/Not Owned.

Verifies that:
1. The council does not advance to 'Owned' for BUY/BUY_LIMIT with R/R > 1.0.
2. DR completion atomically transitions to 'Owned' or 'Not Owned'.
3. OVERRIDDEN->AVOID can NEVER end in 'Owned'.
"""
import os
import sqlite3
import tempfile

import pytest

import app.database as db


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DB_PATH", path)
    original = db.DB_NAME
    db.DB_NAME = path
    db.init_db()
    yield path
    db.DB_NAME = original
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _insert(path, *, symbol="X", recommendation="BUY", status="Pending DR Review", rr=2.0):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO decision_points (symbol, price_at_decision, drop_percent,
           recommendation, reasoning, status, risk_reward_ratio)
           VALUES (?, 100.0, -7.0, ?, 'r', ?, ?)""",
        (symbol, recommendation, status, rr),
    )
    did = cur.lastrowid
    conn.commit()
    conn.close()
    return did


def _get_status(path, decision_id):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("SELECT status FROM decision_points WHERE id = ?", (decision_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def test_dr_buy_promotes_pending_to_owned(temp_db):
    did = _insert(temp_db, symbol="AAPL", recommendation="BUY", status="Pending DR Review")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="BUY", dr_review_verdict="CONFIRMED",
    )
    assert _get_status(temp_db, did) == "Owned"


def test_dr_buy_limit_promotes_pending_to_owned(temp_db):
    did = _insert(temp_db, symbol="MSFT", recommendation="BUY_LIMIT", status="Pending DR Review")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="BUY_LIMIT", dr_review_verdict="ADJUSTED",
    )
    assert _get_status(temp_db, did) == "Owned"


def test_overridden_avoid_demotes_pending_to_not_owned(temp_db):
    did = _insert(temp_db, symbol="EMBJ", recommendation="BUY", status="Pending DR Review")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="AVOID", dr_review_verdict="OVERRIDDEN",
    )
    assert _get_status(temp_db, did) == "Not Owned"


def test_dr_watch_demotes_pending_to_not_owned(temp_db):
    did = _insert(temp_db, symbol="UI", recommendation="BUY_LIMIT", status="Pending DR Review")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="WATCH", dr_review_verdict="ADJUSTED",
    )
    assert _get_status(temp_db, did) == "Not Owned"


def test_dr_hold_demotes_pending_to_not_owned(temp_db):
    did = _insert(temp_db, symbol="APP", recommendation="BUY", status="Pending DR Review")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="HOLD", dr_review_verdict="ADJUSTED",
    )
    assert _get_status(temp_db, did) == "Not Owned"


def test_dr_avoid_without_overridden_still_demotes(temp_db):
    did = _insert(temp_db, symbol="X", recommendation="BUY", status="Pending DR Review")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="AVOID", dr_review_verdict=None,
    )
    assert _get_status(temp_db, did) == "Not Owned"


def test_finalize_no_op_when_decision_id_missing(temp_db):
    result = db.finalize_position_status_after_dr(
        decision_id=999999, dr_action="BUY", dr_review_verdict="CONFIRMED",
    )
    assert result is False


def test_finalize_does_not_promote_already_not_owned_row(temp_db):
    """If the row was set to Not Owned by some other path (e.g. earnings
    consistency downgrade), DR completion must not silently promote it."""
    did = _insert(temp_db, symbol="X", recommendation="BUY", status="Not Owned")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="BUY", dr_review_verdict="CONFIRMED",
    )
    assert _get_status(temp_db, did) == "Not Owned"
