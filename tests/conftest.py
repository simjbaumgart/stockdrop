# tests/conftest.py  (append; do not overwrite existing fixtures)
import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def temp_db(monkeypatch):
    """Fresh sqlite DB with one parent decision_points row.

    Uses monkeypatch.setattr (auto-restores on teardown) so test isolation
    holds even when subsequent tests don't use this fixture. Both
    app.database and the token_tracker module (which dereferences
    app.database.DB_NAME at call time) see the temp path.

    Yields: (path: str, decision_id: int)
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    import app.database as db
    monkeypatch.setattr(db, "DB_NAME", path)
    db.init_db()
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO decision_points (symbol, price_at_decision, drop_percent, "
        "recommendation, reasoning, status) VALUES (?, ?, ?, ?, ?, ?)",
        ("TEST", 100.0, -6.0, "PENDING", "Analyzing...", "Pending"),
    )
    decision_id = cur.lastrowid
    conn.commit()
    conn.close()
    yield path, decision_id
    os.unlink(path)


@pytest.fixture(autouse=True)
def _no_production_db(monkeypatch, tmp_path):
    """Safety net (v0.8.2-288 review #1): no test may touch subscribers.db.

    If the running test's module didn't already redirect app.database to its
    own test DB, point both the module attr and the DB_PATH env var (read at
    call time by deep_research_service) at a throwaway per-test file.
    monkeypatch auto-restores after each test, so cross-module leakage from
    import-time DB_NAME assignments can no longer land on production.
    """
    import app.database as db

    current = os.path.basename(str(db.DB_NAME))
    if current == "subscribers.db":
        guard_db = str(tmp_path / "guard.db")
        monkeypatch.setattr(db, "DB_NAME", guard_db)
        monkeypatch.setenv("DB_PATH", guard_db)
    elif os.getenv("DB_PATH", "subscribers.db") == "subscribers.db":
        # Module redirected DB_NAME but not the env var — align them.
        monkeypatch.setenv("DB_PATH", str(db.DB_NAME))
