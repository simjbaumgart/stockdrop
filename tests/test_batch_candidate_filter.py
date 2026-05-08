"""Batch-comparison candidate selection must exclude rows where deep
research overrode the PM verdict to AVOID."""
import os
import sqlite3
import tempfile
from contextlib import contextmanager

import pytest

import app.database as db


@contextmanager
def _temp_db(monkeypatch_env):
    """Spin up a throwaway sqlite file with the production schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch_env.setenv("DB_PATH", path)
    # database.py reads DB_NAME at import; patch it directly too
    original = db.DB_NAME
    db.DB_NAME = path
    try:
        db.init_db()
        yield path
    finally:
        db.DB_NAME = original
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _insert_row(path, *, symbol, recommendation, dr_verdict, dr_review_verdict, dr_action, ts="2026-05-08 10:00:00"):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO decision_points (symbol, timestamp, recommendation,
            deep_research_verdict, deep_research_review_verdict,
            deep_research_action, deep_research_score,
            price_at_decision, drop_percent)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (symbol, ts, recommendation, dr_verdict, dr_review_verdict, dr_action, 75, 0.0, 5.0),
    )
    conn.commit()
    conn.close()


def test_overridden_avoid_excluded_from_unbatched(monkeypatch):
    with _temp_db(monkeypatch) as path:
        # Eligible: PM=BUY, DR confirmed
        _insert_row(path, symbol="GOOD1", recommendation="BUY",
                    dr_verdict="BUY", dr_review_verdict="CONFIRMED", dr_action="BUY")
        # Eligible: PM=BUY, DR upgraded
        _insert_row(path, symbol="GOOD2", recommendation="BUY",
                    dr_verdict="BUY", dr_review_verdict="UPGRADED", dr_action="BUY")
        # NOT eligible: PM=BUY but DR overrode to AVOID (the EMBJ/APP case)
        _insert_row(path, symbol="OVERRIDE_AVOID", recommendation="BUY",
                    dr_verdict="AVOID", dr_review_verdict="OVERRIDDEN", dr_action="AVOID")
        # NOT eligible: DR action AVOID even without OVERRIDDEN flag
        _insert_row(path, symbol="DR_AVOID", recommendation="BUY",
                    dr_verdict="AVOID", dr_review_verdict="ADJUSTED", dr_action="AVOID")

        rows = db.get_unbatched_candidates_by_date("2026-05-08")

    symbols = {r["symbol"] for r in rows}
    assert symbols == {"GOOD1", "GOOD2"}


def test_distinct_dates_excludes_when_only_overridden_rows_exist(monkeypatch):
    with _temp_db(monkeypatch) as path:
        _insert_row(path, symbol="OVERRIDE_AVOID", recommendation="BUY",
                    dr_verdict="AVOID", dr_review_verdict="OVERRIDDEN", dr_action="AVOID",
                    ts="2026-05-07 10:00:00")
        _insert_row(path, symbol="GOOD", recommendation="BUY",
                    dr_verdict="BUY", dr_review_verdict="CONFIRMED", dr_action="BUY",
                    ts="2026-05-08 10:00:00")

        dates = db.get_distinct_dates_with_unbatched_candidates()

    assert "2026-05-07" not in dates
    assert "2026-05-08" in dates
