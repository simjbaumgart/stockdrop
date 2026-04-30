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
    conn.close()


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
    conn.close()


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
    conn.close()


def test_get_cached_transcript_miss(temp_db):
    from app.database import get_cached_transcript
    assert get_cached_transcript("AAPL", "2026Q1") is None


def test_save_and_get_cached_transcript(temp_db):
    from app.database import save_cached_transcript, get_cached_transcript
    save_cached_transcript(
        symbol="AAPL",
        fiscal_quarter="2026Q1",
        source="alpha_vantage",
        text="Tim Cook here. Good afternoon.",
        report_date="2026-01-30",
    )
    row = get_cached_transcript("AAPL", "2026Q1")
    assert row is not None
    assert row["text"].startswith("Tim Cook")
    assert row["source"] == "alpha_vantage"
    assert row["report_date"] == "2026-01-30"


def test_save_cached_transcript_idempotent(temp_db):
    """Second save with same key must NOT overwrite — first write wins."""
    from app.database import save_cached_transcript, get_cached_transcript
    save_cached_transcript("AAPL", "2026Q1", "defeatbeta", "first", "2026-01-30")
    save_cached_transcript("AAPL", "2026Q1", "alpha_vantage", "second", "2026-01-30")
    row = get_cached_transcript("AAPL", "2026Q1")
    assert row["text"] == "first"
    assert row["source"] == "defeatbeta"
