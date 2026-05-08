"""Load and normalize the decision_points cohort for analysis."""
from __future__ import annotations

import os
import sqlite3
from typing import Optional

import pandas as pd

from app.services.performance_service import normalize_to_intent


def _db_path() -> str:
    return os.getenv("DB_PATH", "subscribers.db")


def load_cohort(start_date: Optional[str] = "2026-02-01") -> pd.DataFrame:
    """
    Return the decision_points table as a DataFrame, filtered to start_date and enriched.

    Adds:
      - decision_date: datetime (date portion of timestamp)
      - intent: normalized recommendation (ENTER_NOW / ENTER_LIMIT / AVOID / NEUTRAL)
    """
    conn = sqlite3.connect(_db_path())
    try:
        df = pd.read_sql_query("SELECT * FROM decision_points", conn)
    finally:
        conn.close()

    if df.empty:
        return df

    df["decision_date"] = pd.to_datetime(df["timestamp"]).dt.normalize()
    df["intent"] = df["recommendation"].apply(normalize_to_intent)

    if start_date is not None:
        df = df[df["decision_date"] >= pd.Timestamp(start_date)].reset_index(drop=True)

    return df
