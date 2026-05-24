"""Read-only export of decision_points + desk_positions for the snapshot package.

Reuses the safety pattern from scripts/analysis/export_database.py: URI
read-only mode, query_only pragma, fail-fast busy timeout — so we can run
this against the live subscribers.db while FastAPI is still serving.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Iterable, List

import pandas as pd

logger = logging.getLogger(__name__)


DECISION_POINTS_ALLOWLIST: List[str] = [
    "id", "symbol", "company_name", "sector", "timestamp",
    "price_at_decision", "drop_percent", "recommendation", "ai_score",
    "conviction", "drop_type",
    "entry_price_low", "entry_price_high", "stop_loss",
    "take_profit_1", "take_profit_2",
    "deep_research_action", "deep_research_score", "deep_research_conviction",
    "deep_research_entry_low", "deep_research_entry_high",
    "deep_research_tp1", "deep_research_tp2",
    "sa_quant_rating", "wall_street_rating",
    "gatekeeper_tier", "batch_winner",
]

EXCLUDED_TABLES = frozenset({"subscribers"})


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only = ON;")
    conn.execute("PRAGMA busy_timeout = 2000;")
    return conn


def _existing_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def load_decisions(db_path: Path, since_days: int, as_of: str) -> pd.DataFrame:
    """Return decisions in [as_of - since_days, as_of], trimmed to the allowlist."""
    conn = _open_readonly(db_path)
    try:
        existing = _existing_columns(conn, "decision_points")
        cols = [c for c in DECISION_POINTS_ALLOWLIST if c in existing]
        col_sql = ", ".join(cols)
        # Use parameter binding for as_of to avoid any chance of injection.
        query = (
            f"SELECT {col_sql} FROM decision_points "
            f"WHERE timestamp >= datetime(?, ?) AND timestamp <= datetime(?) "
            f"ORDER BY timestamp DESC"
        )
        df = pd.read_sql_query(
            query,
            conn,
            params=(as_of, f"-{since_days} days", as_of),
        )
    finally:
        conn.close()
    return df


def load_positions(db_path: Path) -> pd.DataFrame:
    """Return all desk_positions — every column is structured, no allowlist needed."""
    conn = _open_readonly(db_path)
    try:
        df = pd.read_sql_query("SELECT * FROM desk_positions ORDER BY entry_date DESC", conn)
    finally:
        conn.close()
    return df


def _dump_schema(conn: sqlite3.Connection, tables: Iterable[str]) -> str:
    parts: List[str] = []
    for t in tables:
        if t in EXCLUDED_TABLES:
            continue
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone()
        if row and row[0]:
            parts.append(row[0].strip() + ";")
    return "\n\n".join(parts) + "\n"


def export_snapshot_data(
    db_path: Path, out_dir: Path, since_days: int, as_of: str
) -> None:
    """Write decisions.csv, positions.csv, schema.sql to out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    decisions = load_decisions(db_path, since_days=since_days, as_of=as_of)
    positions = load_positions(db_path)
    decisions.to_csv(out_dir / "decisions.csv", index=False)
    positions.to_csv(out_dir / "positions.csv", index=False)

    conn = _open_readonly(db_path)
    try:
        schema_sql = _dump_schema(conn, ["decision_points", "desk_positions"])
    finally:
        conn.close()
    (out_dir / "schema.sql").write_text(schema_sql)

    logger.info(
        "wrote snapshot data: %d decisions, %d positions -> %s",
        len(decisions), len(positions), out_dir,
    )
