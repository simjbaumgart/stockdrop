import sqlite3

import pandas as pd
import pytest

from app.services.analytics.cohort import load_cohort


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE decision_points (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            price_at_decision REAL,
            drop_percent REAL,
            recommendation TEXT,
            timestamp TEXT,
            sector TEXT,
            deep_research_verdict TEXT,
            deep_research_action TEXT,
            entry_price_low REAL,
            entry_price_high REAL,
            stop_loss REAL,
            ai_score REAL,
            gatekeeper_tier TEXT
        )
        """
    )
    rows = [
        (1, "AAPL", 150.0, -6.0, "BUY", "2026-01-15 10:00:00", "Tech", "BUY", "CONFIRM",
         None, None, 140.0, 0.7, "TIER_1"),
        (2, "MSFT", 300.0, -7.0, "BUY_LIMIT", "2026-02-10 10:00:00", "Tech", "AVOID", "OVERRIDE",
         290.0, 295.0, 280.0, 0.6, "TIER_2"),
        (3, "GOOG", 100.0, -5.5, "PASS", "2026-03-01 10:00:00", "Tech", None, None,
         None, None, None, 0.4, "TIER_1"),
    ]
    conn.executemany(
        "INSERT INTO decision_points VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("DB_PATH", str(db_path))
    return db_path


def test_load_cohort_filters_by_date(tmp_db):
    df = load_cohort(start_date="2026-02-01")
    assert len(df) == 2
    assert set(df["symbol"]) == {"MSFT", "GOOG"}


def test_load_cohort_full_history(tmp_db):
    df = load_cohort(start_date=None)
    assert len(df) == 3


def test_load_cohort_columns(tmp_db):
    df = load_cohort(start_date="2026-02-01")
    expected = {
        "id", "symbol", "price_at_decision", "drop_percent", "recommendation",
        "timestamp", "decision_date", "intent", "deep_research_verdict",
        "deep_research_action", "sector", "entry_price_low", "entry_price_high",
        "stop_loss", "ai_score", "gatekeeper_tier",
    }
    assert expected.issubset(set(df.columns))
    assert df["intent"].iloc[0] in {"ENTER_NOW", "ENTER_LIMIT", "AVOID", "NEUTRAL"}
    assert pd.api.types.is_datetime64_any_dtype(df["decision_date"])
