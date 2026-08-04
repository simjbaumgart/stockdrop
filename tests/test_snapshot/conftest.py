"""Shared fixtures for snapshot tests.

Builds a small in-memory-ish SQLite DB (a tmp file, since some code opens
in URI read-only mode) with realistic shape for decision_points,
desk_positions, and subscribers.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest


def _schema() -> str:
    # Mirror the production schema for the columns under test. Other
    # columns are allowed to exist but are not required for these tests.
    return """
    CREATE TABLE decision_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        company_name TEXT,
        sector TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        price_at_decision REAL NOT NULL,
        drop_percent REAL NOT NULL,
        recommendation TEXT NOT NULL,
        ai_score REAL,
        reasoning TEXT,                  -- LLM free text, must be dropped
        deep_research_reason TEXT,       -- LLM free text, must be dropped
        deep_research_swot TEXT,         -- LLM free text, must be dropped
        deep_research_action TEXT,
        deep_research_score INTEGER,
        gatekeeper_tier TEXT
    );

    CREATE TABLE desk_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        decision_point_id INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        entry_date TEXT NOT NULL,
        entry_price REAL NOT NULL,
        position_size REAL NOT NULL,
        attractiveness_score REAL NOT NULL,
        current_price REAL,
        unrealized_pnl_pct REAL,
        exit_date TEXT,
        exit_price REAL,
        realized_pnl_pct REAL,
        exit_reason TEXT
    );

    CREATE TABLE subscribers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """


@pytest.fixture
def snapshot_db(tmp_path) -> Path:
    """Tmp SQLite DB with realistic rows across a 60-day window."""
    db_path = tmp_path / "snapshot_test.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_schema())

    now = datetime(2026, 5, 24, 12, 0, 0)
    rows_in_window = [
        # (symbol, sector, days_ago, drop_pct, rec, ai_score, dr_action, dr_score, tier)
        ("AAPL", "Technology", 5,  -7.2, "BUY",       82, "STRONG_BUY", 88, "TIER_1"),
        ("MSFT", "Technology", 10, -5.5, "BUY_LIMIT", 71, "BUY_LIMIT",  74, "TIER_1"),
        ("JPM",  "Financials", 15, -6.1, "WATCH",     55, None,         None, "TIER_2"),
        ("XOM",  "Energy",     20, -8.4, "AVOID",     22, "AVOID",      18, "TIER_3"),
        ("NVDA", "Technology", 25, -5.2, "BUY",       77, "BUY",        80, "TIER_1"),
        ("PFE",  "Healthcare", 28, -9.0, "AVOID",     30, "AVOID",      25, "TIER_2"),
    ]
    rows_outside_window = [
        ("TSLA", "Consumer", 45, -6.0, "BUY", 60, "BUY", 65, "TIER_2"),  # >30d ago
    ]
    for symbol, sector, days_ago, drop, rec, score, dr_act, dr_score, tier in rows_in_window + rows_outside_window:
        ts = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO decision_points
              (symbol, company_name, sector, timestamp, price_at_decision, drop_percent,
               recommendation, ai_score, reasoning, deep_research_reason, deep_research_swot,
               deep_research_action, deep_research_score, gatekeeper_tier)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (symbol, f"{symbol} Inc.", sector, ts, 100.0, drop, rec, score,
             "Bull case: ...", "DR says: ...", "Strengths: ...", dr_act, dr_score, tier),
        )

    # desk_positions: 4 closed (2 wins, 2 losses), 2 open
    positions = [
        # (dp_id, ticker, status, entry_days_ago, entry_price, current_price, exit_days_ago, exit_price, realized_pct, reason)
        (1, "AAPL", "CLOSED", 5,  92.8,  None, 1,  101.2, 9.05,  "TP1"),
        (2, "MSFT", "CLOSED", 10, 94.5,  None, 2,  87.1,  -7.83, "STOP"),
        (5, "NVDA", "CLOSED", 25, 94.8,  None, 5,  108.6, 14.56, "TP2"),
        (6, "PFE",  "CLOSED", 28, 91.0,  None, 10, 86.5,  -4.95, "STOP"),
        (1, "AAPL", "ACTIVE", 5,  92.8,  98.3, None, None, None, None),
        (5, "NVDA", "ACTIVE", 25, 94.8,  103.1, None, None, None, None),
    ]
    for dp_id, ticker, status, entry_days, entry_p, cur_p, exit_days, exit_p, realized, reason in positions:
        entry_date = (now - timedelta(days=entry_days)).strftime("%Y-%m-%d")
        exit_date = (now - timedelta(days=exit_days)).strftime("%Y-%m-%d") if exit_days is not None else None
        unrealized = ((cur_p - entry_p) / entry_p * 100) if cur_p is not None else None
        conn.execute(
            """
            INSERT INTO desk_positions
              (decision_point_id, ticker, status, entry_date, entry_price, position_size,
               attractiveness_score, current_price, unrealized_pnl_pct,
               exit_date, exit_price, realized_pnl_pct, exit_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (dp_id, ticker, status, entry_date, entry_p, 1000.0, 75.0,
             cur_p, unrealized, exit_date, exit_p, realized, reason),
        )

    # subscribers — must NEVER appear in export
    conn.execute("INSERT INTO subscribers (email) VALUES ('private@example.com')")

    conn.commit()
    conn.close()
    return db_path
