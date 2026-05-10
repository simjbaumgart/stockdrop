"""Load and normalize the decision_points cohort for analysis."""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Optional

import pandas as pd

from app.services.performance_service import normalize_to_intent

# Synthetic / placeholder symbols created during dev or testing. Real tickers use
# `.` or `-` for share-class separators, never `_`, so anything containing an
# underscore is treated as fixture data. The bare "TEST" symbol is also dropped.
_BARE_TEST_RE = re.compile(r"^TEST$", re.IGNORECASE)


def _is_test_symbol(symbol: object) -> bool:
    if symbol is None:
        return False
    s = str(symbol).strip()
    if not s:
        return False
    if "_" in s:
        return True
    return bool(_BARE_TEST_RE.match(s))


def _db_path() -> str:
    return os.getenv("DB_PATH", "subscribers.db")


def load_cohort(start_date: Optional[str] = "2026-02-01") -> pd.DataFrame:
    """
    Return the decision_points table as a DataFrame, filtered to start_date and enriched.

    Adds:
      - decision_date: datetime (date portion of timestamp)
      - intent: normalized recommendation (ENTER_NOW / ENTER_LIMIT / AVOID / NEUTRAL)

    Synthetic test symbols (TEST, TEST_T3, etc.) are excluded.
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

    # Drop synthetic test symbols
    test_mask = df["symbol"].apply(_is_test_symbol)
    if test_mask.any():
        df = df.loc[~test_mask].reset_index(drop=True)

    if start_date is not None:
        df = df[df["decision_date"] >= pd.Timestamp(start_date)].reset_index(drop=True)

    return df
