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
