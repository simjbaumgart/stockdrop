import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def temp_db(monkeypatch):
    """Use an isolated DB file so the test never touches subscribers.db."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DB_PATH", path)
    # Re-import to pick up the env var
    import importlib
    import app.database as db
    importlib.reload(db)
    db.init_db()
    yield path
    os.unlink(path)


def test_transcript_cache_table_exists(temp_db):
    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcript_cache'")
    assert cur.fetchone() is not None, "transcript_cache table missing after init_db()"


def test_transcript_cache_columns(temp_db):
    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(transcript_cache)")
    cols = {row[1]: row[2] for row in cur.fetchall()}
    assert cols.get("symbol") == "TEXT"
    assert cols.get("fiscal_quarter") == "TEXT"
    assert cols.get("source") == "TEXT"
    assert cols.get("text") == "TEXT"
    assert cols.get("report_date") == "TEXT"
    assert cols.get("fetched_at") == "TIMESTAMP"


def test_transcript_cache_unique_key(temp_db):
    """(symbol, fiscal_quarter) must be unique — re-inserts must fail."""
    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transcript_cache (symbol, fiscal_quarter, source, text, report_date) "
        "VALUES (?, ?, ?, ?, ?)",
        ("AAPL", "2026Q1", "alpha_vantage", "hello", "2026-01-30"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute(
            "INSERT INTO transcript_cache (symbol, fiscal_quarter, source, text, report_date) "
            "VALUES (?, ?, ?, ?, ?)",
            ("AAPL", "2026Q1", "defeatbeta", "different", "2026-01-30"),
        )
        conn.commit()
