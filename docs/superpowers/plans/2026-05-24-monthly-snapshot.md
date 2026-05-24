# Monthly Browsable Data Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-command snapshot generator that publishes the last 30 days of StockDrop decisions + outcomes as a browsable package at `docs/performance/2026-05-24-package/`, then run it and ship the result.

**Architecture:** A small `app/services/snapshot/` package holds pure, testable units — filtered DB export, aggregates, charts, template rendering, case-study drafting. A thin orchestration script `scripts/build_monthly_snapshot.py` wires them together. Reuses the read-only SQLite pattern from `scripts/analysis/export_database.py`. Charts via matplotlib (Agg backend), templating via Jinja2 — both already on the project's dependency surface.

**Tech Stack:** Python 3.9, sqlite3 (stdlib, URI read-only mode), pandas 2.3.3, matplotlib (Agg), jinja2 3.1.6, pytest, pytest-asyncio.

**Spec:** [docs/superpowers/specs/2026-05-24-monthly-snapshot-design.md](../specs/2026-05-24-monthly-snapshot-design.md)

---

## File Structure

**Create:**
- `app/services/snapshot/__init__.py` — package marker
- `app/services/snapshot/db_export.py` — read-only DB → trimmed DataFrames + column allowlist
- `app/services/snapshot/aggregates.py` — `build_monthly_summary`, `compute_headline_stats`
- `app/services/snapshot/charts.py` — four pure chart functions, each writes one PNG
- `app/services/snapshot/render.py` — Jinja2 template loader + render helpers
- `app/services/snapshot/case_studies.py` — pick top candidates, draft markdown stubs
- `app/services/snapshot/templates/README.md.j2` — package landing-page template
- `app/services/snapshot/templates/data_README.md.j2` — data-dictionary template
- `scripts/build_monthly_snapshot.py` — orchestration entry point
- `tests/test_snapshot/__init__.py`
- `tests/test_snapshot/conftest.py` — builds an in-memory fixture DB
- `tests/test_snapshot/test_db_export.py`
- `tests/test_snapshot/test_aggregates.py`
- `tests/test_snapshot/test_charts.py`
- `tests/test_snapshot/test_render.py`
- `tests/test_snapshot/test_case_studies.py`
- `tests/test_snapshot/test_integration.py`

**Modify:**
- `README.md` — add a "📊 Monthly snapshot" section linking to the latest package

**Generated at run time (then committed):**
- `docs/performance/2026-05-24-package/README.md`
- `docs/performance/2026-05-24-package/charts/{verdict-distribution,sector-breakdown,pnl-distribution,score-vs-outcome}.png`
- `docs/performance/2026-05-24-package/case-studies/{01-best-trade,02-worst-trade,03-avoided-correctly,04-still-open}.md`
- `docs/performance/2026-05-24-package/data/{README.md,decisions.csv,positions.csv,monthly_summary.csv,schema.sql,manifest.csv}`

---

## Allowlist constants (referenced by multiple tasks)

These are defined once in `app/services/snapshot/db_export.py` and imported elsewhere.

```python
# app/services/snapshot/db_export.py — top of file
DECISION_POINTS_ALLOWLIST = [
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

EXCLUDED_TABLES = frozenset({"subscribers"})  # guardrail — never export
```

---

## Task 1: Test fixture DB builder

**Files:**
- Create: `tests/test_snapshot/__init__.py`
- Create: `tests/test_snapshot/conftest.py`

- [ ] **Step 1: Create empty package marker**

```python
# tests/test_snapshot/__init__.py
```

- [ ] **Step 2: Write the fixture conftest**

```python
# tests/test_snapshot/conftest.py
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
```

- [ ] **Step 3: Sanity-check the fixture loads**

Run: `pytest tests/test_snapshot/ -v --collect-only 2>&1 | head -20`
Expected: pytest collects no tests yet (only conftest exists), exits 5 ("no tests ran") — that's fine.

- [ ] **Step 4: Commit**

```bash
git add tests/test_snapshot/__init__.py tests/test_snapshot/conftest.py
git commit -m "test(snapshot): add fixture DB builder for snapshot tests"
```

---

## Task 2: Filtered, allowlisted DB export

**Files:**
- Create: `app/services/snapshot/__init__.py`
- Create: `app/services/snapshot/db_export.py`
- Create: `tests/test_snapshot/test_db_export.py`

- [ ] **Step 1: Empty package marker**

```python
# app/services/snapshot/__init__.py
```

- [ ] **Step 2: Write failing tests for db_export**

```python
# tests/test_snapshot/test_db_export.py
"""Tests for the snapshot DB export: time filter, column allowlist, privacy guard."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from app.services.snapshot.db_export import (
    DECISION_POINTS_ALLOWLIST,
    EXCLUDED_TABLES,
    export_snapshot_data,
    load_decisions,
    load_positions,
)


BANNED_LLM_COLUMNS = {
    "reasoning",
    "deep_research_reason",
    "deep_research_swot",
    "deep_research_risk",
    "deep_research_catalyst",
    "deep_research_knife_catch",
    "deep_research_global_analysis",
    "deep_research_local_analysis",
    "deep_research_verification",
    "deep_research_blindspots",
    "reassess_reasoning",
    "deep_research_review_verdict",
}


def test_load_decisions_respects_time_window(snapshot_db: Path):
    df = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    # Fixture has 6 rows in-window, 1 row 45 days ago (TSLA) — that must be excluded.
    assert len(df) == 6
    assert "TSLA" not in df["symbol"].values


def test_load_decisions_only_returns_allowlisted_columns(snapshot_db: Path):
    df = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    extra = set(df.columns) - set(DECISION_POINTS_ALLOWLIST)
    assert not extra, f"unexpected columns: {extra}"
    leaked = BANNED_LLM_COLUMNS.intersection(df.columns)
    assert not leaked, f"LLM free-text columns leaked: {leaked}"


def test_load_decisions_skips_missing_allowlisted_cols_silently(snapshot_db: Path, tmp_path):
    """If the DB doesn't have an allowlisted column (e.g. older schema),
    the export should still succeed — that column just won't be in the output."""
    df = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    # The fixture omits batch_winner; it should not raise.
    assert "batch_winner" not in df.columns


def test_load_positions_returns_all_columns(snapshot_db: Path):
    df = load_positions(snapshot_db)
    # Fixture has 6 positions
    assert len(df) == 6
    # Structured columns we rely on downstream
    for col in ("ticker", "status", "entry_price", "realized_pnl_pct", "current_price"):
        assert col in df.columns


def test_subscribers_table_never_exported(snapshot_db: Path, tmp_path):
    out_dir = tmp_path / "out"
    export_snapshot_data(snapshot_db, out_dir, since_days=30, as_of="2026-05-24")
    # Even if a future caller asks for "all tables", subscribers must be filtered.
    assert not (out_dir / "subscribers.csv").exists()
    assert "subscribers" in EXCLUDED_TABLES


def test_export_writes_expected_files(snapshot_db: Path, tmp_path):
    out_dir = tmp_path / "out"
    export_snapshot_data(snapshot_db, out_dir, since_days=30, as_of="2026-05-24")
    assert (out_dir / "decisions.csv").exists()
    assert (out_dir / "positions.csv").exists()
    assert (out_dir / "schema.sql").exists()
    # schema.sql contains both shipped tables
    schema_text = (out_dir / "schema.sql").read_text()
    assert "CREATE TABLE decision_points" in schema_text
    assert "CREATE TABLE desk_positions" in schema_text
    assert "subscribers" not in schema_text  # subscribers table never appears
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `pytest tests/test_snapshot/test_db_export.py -v`
Expected: 6 errors — `ModuleNotFoundError: No module named 'app.services.snapshot.db_export'`

- [ ] **Step 4: Implement db_export**

```python
# app/services/snapshot/db_export.py
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
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `pytest tests/test_snapshot/test_db_export.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add app/services/snapshot/__init__.py app/services/snapshot/db_export.py tests/test_snapshot/test_db_export.py
git commit -m "feat(snapshot): filtered, allowlisted DB export with privacy guard"
```

---

## Task 3: Aggregates module

**Files:**
- Create: `app/services/snapshot/aggregates.py`
- Create: `tests/test_snapshot/test_aggregates.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_snapshot/test_aggregates.py
"""Tests for monthly summary aggregation and headline stats."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.services.snapshot.aggregates import (
    build_monthly_summary,
    compute_headline_stats,
)
from app.services.snapshot.db_export import load_decisions, load_positions


def test_monthly_summary_has_one_row_per_verdict(snapshot_db: Path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    summary = build_monthly_summary(decisions, positions)
    # Fixture has 4 distinct recommendations in-window: BUY, BUY_LIMIT, WATCH, AVOID
    assert set(summary["recommendation"]) == {"BUY", "BUY_LIMIT", "WATCH", "AVOID"}


def test_monthly_summary_win_rate_and_pnl(snapshot_db: Path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    summary = build_monthly_summary(decisions, positions)
    buy_row = summary[summary["recommendation"] == "BUY"].iloc[0]
    # Fixture: 2 BUYs (AAPL, NVDA), both have closed positions, both profitable
    assert buy_row["n_closed"] == 2
    assert buy_row["win_rate"] == 1.0
    # mean of 9.05% and 14.56% = 11.805%
    assert abs(buy_row["mean_realized_pnl_pct"] - 11.805) < 0.01


def test_monthly_summary_handles_zero_closed(snapshot_db: Path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    summary = build_monthly_summary(decisions, positions)
    watch_row = summary[summary["recommendation"] == "WATCH"].iloc[0]
    # WATCH (JPM) has no desk_position — win_rate should be None/NaN, not crash
    assert watch_row["n_closed"] == 0
    assert pd.isna(watch_row["win_rate"])


def test_headline_stats_returns_string_dict(snapshot_db: Path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    stats = compute_headline_stats(decisions, positions, as_of="2026-05-24", since_days=30)
    # Required keys for the README template
    for key in (
        "as_of", "window_start", "window_end", "total_decisions",
        "n_buy", "n_buy_limit", "n_watch", "n_avoid",
        "n_positions_total", "n_positions_closed", "n_positions_open",
        "overall_win_rate", "mean_realized_pnl_pct",
    ):
        assert key in stats, f"missing key {key}"
        assert isinstance(stats[key], str), f"{key} must be a pre-formatted string for the template"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_snapshot/test_aggregates.py -v`
Expected: errors — `ModuleNotFoundError: No module named 'app.services.snapshot.aggregates'`

- [ ] **Step 3: Implement aggregates**

```python
# app/services/snapshot/aggregates.py
"""Aggregate decisions + positions into the monthly_summary CSV and headline stats."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict

import pandas as pd


def _format_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:+.2f}%"


def _format_count(value: float | int) -> str:
    if pd.isna(value):
        return "0"
    return str(int(value))


def build_monthly_summary(decisions: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """One row per recommendation: counts + outcome metrics from desk_positions.

    Joins on decisions.id == positions.decision_point_id. Open positions
    contribute to counts but not to win_rate / mean_realized_pnl_pct.
    """
    if decisions.empty:
        return pd.DataFrame(
            columns=[
                "recommendation", "count", "mean_drop_pct", "mean_ai_score",
                "n_with_positions", "n_closed", "win_rate", "mean_realized_pnl_pct",
            ]
        )

    # decisions -> verdict counts and means
    base = (
        decisions.groupby("recommendation")
        .agg(
            count=("id", "count"),
            mean_drop_pct=("drop_percent", "mean"),
            mean_ai_score=("ai_score", "mean"),
        )
        .reset_index()
    )

    # join positions on decision_point_id -> id.
    # suffixes=("", "_dec") keeps positions.id as "id" (and renames
    # decisions.id to "id_dec") so the agg below can reference "id"
    # without a column-collision KeyError.
    joined = positions.merge(
        decisions[["id", "recommendation"]],
        left_on="decision_point_id",
        right_on="id",
        how="inner",
        suffixes=("", "_dec"),
    )
    closed = joined[joined["status"] == "CLOSED"]
    pos_agg = (
        joined.groupby("recommendation")
        .agg(n_with_positions=("id", "count"))
        .reset_index()
    )
    closed_agg = (
        closed.groupby("recommendation")
        .agg(
            n_closed=("id", "count"),
            mean_realized_pnl_pct=("realized_pnl_pct", "mean"),
            win_rate=("realized_pnl_pct", lambda s: (s > 0).mean()),
        )
        .reset_index()
    )

    out = base.merge(pos_agg, on="recommendation", how="left").merge(
        closed_agg, on="recommendation", how="left"
    )
    out["n_with_positions"] = out["n_with_positions"].fillna(0).astype(int)
    out["n_closed"] = out["n_closed"].fillna(0).astype(int)
    return out


def compute_headline_stats(
    decisions: pd.DataFrame,
    positions: pd.DataFrame,
    as_of: str,
    since_days: int,
) -> Dict[str, str]:
    """Return template-ready stat strings (already formatted, never None)."""
    end = datetime.strptime(as_of, "%Y-%m-%d")
    start = end - timedelta(days=since_days)

    closed = positions[positions["status"] == "CLOSED"] if not positions.empty else positions
    open_pos = positions[positions["status"] == "ACTIVE"] if not positions.empty else positions

    counts = decisions["recommendation"].value_counts() if not decisions.empty else pd.Series(dtype=int)
    win_rate = (closed["realized_pnl_pct"] > 0).mean() if not closed.empty else None
    mean_pnl = closed["realized_pnl_pct"].mean() if not closed.empty else None

    return {
        "as_of": as_of,
        "window_start": start.strftime("%Y-%m-%d"),
        "window_end": end.strftime("%Y-%m-%d"),
        "total_decisions": _format_count(len(decisions)),
        "n_buy": _format_count(counts.get("BUY", 0)),
        "n_buy_limit": _format_count(counts.get("BUY_LIMIT", 0)),
        "n_watch": _format_count(counts.get("WATCH", 0)),
        "n_avoid": _format_count(counts.get("AVOID", 0)),
        "n_positions_total": _format_count(len(positions)),
        "n_positions_closed": _format_count(len(closed)),
        "n_positions_open": _format_count(len(open_pos)),
        "overall_win_rate": "—" if win_rate is None else f"{win_rate * 100:.1f}%",
        "mean_realized_pnl_pct": _format_pct(mean_pnl),
    }
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_snapshot/test_aggregates.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/snapshot/aggregates.py tests/test_snapshot/test_aggregates.py
git commit -m "feat(snapshot): monthly summary aggregates and headline stats"
```

---

## Task 4: Charts module

**Files:**
- Create: `app/services/snapshot/charts.py`
- Create: `tests/test_snapshot/test_charts.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_snapshot/test_charts.py
"""Tests for the four PNG chart renderers."""
from __future__ import annotations

from pathlib import Path

from app.services.snapshot.charts import (
    chart_pnl_distribution,
    chart_score_vs_outcome,
    chart_sector_breakdown,
    chart_verdict_distribution,
)
from app.services.snapshot.db_export import load_decisions, load_positions


def _assert_png(path: Path):
    assert path.exists(), f"{path} not written"
    assert path.stat().st_size > 1000, f"{path} suspiciously small"
    # PNG signature: 0x89 'PNG\r\n\x1a\n'
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"


def test_chart_verdict_distribution(snapshot_db: Path, tmp_path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    out = tmp_path / "verdict.png"
    chart_verdict_distribution(decisions, out)
    _assert_png(out)


def test_chart_sector_breakdown(snapshot_db: Path, tmp_path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    out = tmp_path / "sector.png"
    chart_sector_breakdown(decisions, out)
    _assert_png(out)


def test_chart_pnl_distribution(snapshot_db: Path, tmp_path):
    positions = load_positions(snapshot_db)
    out = tmp_path / "pnl.png"
    chart_pnl_distribution(positions, out)
    _assert_png(out)


def test_chart_score_vs_outcome(snapshot_db: Path, tmp_path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    out = tmp_path / "scatter.png"
    chart_score_vs_outcome(decisions, positions, out)
    _assert_png(out)


def test_chart_handles_empty_input(tmp_path):
    """Should write a placeholder PNG rather than crash on empty data."""
    import pandas as pd
    out = tmp_path / "empty.png"
    chart_verdict_distribution(pd.DataFrame(columns=["recommendation"]), out)
    _assert_png(out)
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_snapshot/test_charts.py -v`
Expected: errors — `ModuleNotFoundError: No module named 'app.services.snapshot.charts'`

- [ ] **Step 3: Implement charts**

```python
# app/services/snapshot/charts.py
"""Pure chart functions. Each takes data + output path, writes one PNG."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

FIGSIZE = (12, 8)
DPI = 100

VERDICT_COLORS = {
    "BUY": "#22c55e",
    "BUY_LIMIT": "#3b82f6",
    "WATCH": "#f59e0b",
    "AVOID": "#ef4444",
    "PASS_INSUFFICIENT_DATA": "#94a3b8",
}


def _empty_chart(out: Path, message: str) -> None:
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=18, color="#64748b")
    ax.set_axis_off()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def chart_verdict_distribution(decisions: pd.DataFrame, out: Path) -> None:
    if decisions.empty:
        _empty_chart(out, "No decisions in window")
        return
    counts = decisions["recommendation"].value_counts().sort_values()
    colors = [VERDICT_COLORS.get(v, "#94a3b8") for v in counts.index]
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.barh(counts.index, counts.values, color=colors)
    for i, v in enumerate(counts.values):
        ax.text(v + 0.5, i, str(v), va="center")
    ax.set_xlabel("Number of decisions")
    ax.set_title("PM Verdict distribution (last 30 days)")
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def chart_sector_breakdown(decisions: pd.DataFrame, out: Path) -> None:
    if decisions.empty or "sector" not in decisions.columns:
        _empty_chart(out, "No sector data")
        return
    counts = decisions["sector"].fillna("Unknown").value_counts().head(12).sort_values()
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.barh(counts.index, counts.values, color="#3b82f6")
    for i, v in enumerate(counts.values):
        ax.text(v + 0.1, i, str(v), va="center")
    ax.set_xlabel("Number of decisions")
    ax.set_title("Decisions by sector (top 12, last 30 days)")
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def chart_pnl_distribution(positions: pd.DataFrame, out: Path) -> None:
    closed = positions[positions["status"] == "CLOSED"] if not positions.empty else positions
    if closed.empty:
        _empty_chart(out, "No closed positions yet")
        return
    values = closed["realized_pnl_pct"].dropna()
    if values.empty:
        _empty_chart(out, "No realized P&L data")
        return
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.hist(values, bins=15, color="#3b82f6", edgecolor="white")
    ax.axvline(0, color="#64748b", linestyle="--", linewidth=1, label="Break-even")
    ax.axvline(values.mean(), color="#22c55e", linestyle="-", linewidth=2,
               label=f"Mean: {values.mean():+.2f}%")
    ax.set_xlabel("Realized P&L (%)")
    ax.set_ylabel("Number of closed positions")
    ax.set_title("Realized P&L distribution — closed positions")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def chart_score_vs_outcome(
    decisions: pd.DataFrame, positions: pd.DataFrame, out: Path
) -> None:
    if decisions.empty or positions.empty:
        _empty_chart(out, "No data for score-vs-outcome")
        return
    closed = positions[positions["status"] == "CLOSED"]
    if closed.empty:
        _empty_chart(out, "No closed positions yet")
        return
    joined = closed.merge(
        decisions[["id", "ai_score"]],
        left_on="decision_point_id",
        right_on="id",
        how="inner",
    ).dropna(subset=["ai_score", "realized_pnl_pct"])

    if len(joined) < 3:
        _empty_chart(out, f"Only {len(joined)} closed positions — too few to plot")
        return

    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.scatter(joined["ai_score"], joined["realized_pnl_pct"], s=80, alpha=0.7, color="#3b82f6")

    # Regression line + Pearson r
    x = joined["ai_score"].values
    y = joined["realized_pnl_pct"].values
    if np.std(x) > 0:
        slope, intercept = np.polyfit(x, y, 1)
        xs = np.linspace(x.min(), x.max(), 50)
        ax.plot(xs, slope * xs + intercept, color="#ef4444", linewidth=1.5, linestyle="--")
        r = float(np.corrcoef(x, y)[0, 1])
        note = f"Pearson r = {r:+.2f}, n = {len(joined)}"
    else:
        note = f"n = {len(joined)}"

    if len(joined) < 10:
        note += "  (small sample)"

    ax.axhline(0, color="#64748b", linestyle=":", linewidth=1)
    ax.set_xlabel("AI score (0–100)")
    ax.set_ylabel("Realized P&L (%)")
    ax.set_title("AI score vs. realized outcome")
    ax.text(0.02, 0.97, note, transform=ax.transAxes, va="top", fontsize=11,
            bbox=dict(facecolor="white", edgecolor="#e2e8f0"))
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_snapshot/test_charts.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/snapshot/charts.py tests/test_snapshot/test_charts.py
git commit -m "feat(snapshot): four PNG chart renderers with empty-data fallback"
```

---

## Task 5: Template rendering

**Files:**
- Create: `app/services/snapshot/templates/README.md.j2`
- Create: `app/services/snapshot/templates/data_README.md.j2`
- Create: `app/services/snapshot/render.py`
- Create: `tests/test_snapshot/test_render.py`

- [ ] **Step 1: Write the package landing-page template**

```jinja
{# app/services/snapshot/templates/README.md.j2 #}
# StockDrop — Monthly Snapshot ({{ window_start }} → {{ window_end }})

> Generated {{ as_of }} from `subscribers.db`. Browse the data, charts, and case studies below.

This snapshot captures **{{ total_decisions }} decisions** the AI council made over the trailing 30 days, plus the realized/unrealized outcomes of every position the desk took during that window.

The free-text reasoning from the LLM agents is **deliberately excluded** — this snapshot is about the numbers, not the model's prose.

---

## At a glance

| Metric | Value |
|---|---|
| Decisions | {{ total_decisions }} (BUY: {{ n_buy }}, BUY_LIMIT: {{ n_buy_limit }}, WATCH: {{ n_watch }}, AVOID: {{ n_avoid }}) |
| Positions taken | {{ n_positions_total }} ({{ n_positions_closed }} closed, {{ n_positions_open }} open) |
| Win rate (closed) | {{ overall_win_rate }} |
| Mean realized P&L | {{ mean_realized_pnl_pct }} |

## Charts

### Verdict distribution
![Verdict distribution](charts/verdict-distribution.png)

### Sector breakdown
![Sector breakdown](charts/sector-breakdown.png)

### Realized P&L distribution
![P&L distribution](charts/pnl-distribution.png)

### AI score vs. realized outcome
![Score vs outcome](charts/score-vs-outcome.png)

## Case studies

- [Best trade]( case-studies/01-best-trade.md )
- [Worst trade]( case-studies/02-worst-trade.md )
- [Correctly avoided]( case-studies/03-avoided-correctly.md )
- [Still open]( case-studies/04-still-open.md )

## Raw data

- [decisions.csv](data/decisions.csv) — the ~25 structured columns per decision
- [positions.csv](data/positions.csv) — every position taken, with entry/exit and P&L
- [monthly_summary.csv](data/monthly_summary.csv) — pre-aggregated counts and win rate by verdict
- [schema.sql](data/schema.sql) — `CREATE TABLE` for the shipped tables
- [Data dictionary](data/README.md) — column-by-column explanation

## How this snapshot was built

```bash
python scripts/build_monthly_snapshot.py --as-of {{ as_of }}
```

The script reads `subscribers.db` in read-only mode (`?mode=ro`), filters to the last 30 days, drops every free-text LLM column, renders the charts, fills this template, and writes everything to `docs/performance/{{ as_of }}-package/`.

[Spec](../../superpowers/specs/2026-05-24-monthly-snapshot-design.md) · [Plan](../../superpowers/plans/2026-05-24-monthly-snapshot.md)
```

- [ ] **Step 2: Write the data-dictionary template**

```jinja
{# app/services/snapshot/templates/data_README.md.j2 #}
# Data dictionary

Generated {{ as_of }} from `subscribers.db` (last {{ since_days }} days).

## Files

| File | Source table | Rows | Notes |
|---|---|---:|---|
| `decisions.csv` | `decision_points` | {{ n_decisions }} | Structured columns only — every free-text LLM field is dropped |
| `positions.csv` | `desk_positions` | {{ n_positions }} | All columns are structured |
| `monthly_summary.csv` | (aggregated) | {{ n_summary }} | One row per recommendation × outcome stats |
| `schema.sql` | (DDL) | — | `CREATE TABLE` statements for the two shipped tables |
| `manifest.csv` | (this file's listing) | — | Generated-at timestamp + file row counts |

## `decisions.csv` columns

| Column | Type | Description |
|---|---|---|
| `id` | int | Primary key from `decision_points` |
| `symbol` | str | Ticker symbol |
| `company_name` | str | Issuer name |
| `sector` | str | GICS sector |
| `timestamp` | str | When the decision was made (UTC) |
| `price_at_decision` | float | Last trade price when the screener flagged the drop |
| `drop_percent` | float | Single-day % drop that triggered the screener |
| `recommendation` | str | PM verdict: `BUY` / `BUY_LIMIT` / `WATCH` / `AVOID` / `PASS_INSUFFICIENT_DATA` |
| `ai_score` | float | PM score, 0–100 |
| `conviction` | str | PM conviction band |
| `drop_type` | str | PM's classification of the drop (e.g. earnings, sector, idiosyncratic) |
| `entry_price_low` / `entry_price_high` | float | PM's recommended entry range |
| `stop_loss` | float | Recommended stop price |
| `take_profit_1` / `take_profit_2` | float | First and second take-profit targets |
| `deep_research_action` | str | DR's overriding action (may differ from PM) |
| `deep_research_score` | int | DR's 0–100 score |
| `deep_research_conviction` | str | DR conviction band |
| `deep_research_entry_low/high`, `deep_research_tp1/tp2` | float | DR's price plan (may differ from PM's) |
| `sa_quant_rating`, `wall_street_rating` | float | External ratings snapshotted at decision time |
| `gatekeeper_tier` | str | Pre-filter tier (TIER_1 = strongest setup) |
| `batch_winner` | bool | True if the candidate won its batch comparison |

## `positions.csv` columns

| Column | Type | Description |
|---|---|---|
| `id` | int | Primary key |
| `decision_point_id` | int | FK to `decisions.csv` `id` |
| `ticker` | str | Position ticker |
| `status` | str | `ACTIVE` or `CLOSED` |
| `entry_date`, `entry_price` | str / float | Fill information |
| `position_size` | float | Dollar size of the position |
| `attractiveness_score` | float | Composite score used at sizing time |
| `current_price`, `unrealized_pnl_pct` | float | Snapshot of current state (NULL when closed) |
| `exit_date`, `exit_price`, `realized_pnl_pct`, `exit_reason` | str / float | Closeout information (NULL when active) |

## What's NOT in this snapshot

| Excluded | Why |
|---|---|
| Free-text agent reasoning (`reasoning`, `deep_research_*_analysis`, `deep_research_swot`, etc.) | This snapshot is about numbers — the prose belongs in the case studies, hand-curated, not bulk-dumped |
| `subscribers` table | PII guardrail, regardless of row count |
| `decision_tracking` | Currently empty in the live DB; outcome data comes from `positions.csv` |
| `batch_comparisons`, `desk_reviews`, `transcript_cache` | Internal bookkeeping, not useful to external readers |
```

- [ ] **Step 3: Write failing tests for the renderer**

```python
# tests/test_snapshot/test_render.py
"""Tests for Jinja2 template rendering."""
from __future__ import annotations

from app.services.snapshot.render import render_data_readme, render_package_readme


def _stub_headline_stats():
    return {
        "as_of": "2026-05-24",
        "window_start": "2026-04-24",
        "window_end": "2026-05-24",
        "total_decisions": "404",
        "n_buy": "57",
        "n_buy_limit": "50",
        "n_watch": "62",
        "n_avoid": "191",
        "n_positions_total": "45",
        "n_positions_closed": "20",
        "n_positions_open": "25",
        "overall_win_rate": "55.0%",
        "mean_realized_pnl_pct": "+3.20%",
    }


def test_package_readme_renders_stats():
    text = render_package_readme(_stub_headline_stats())
    assert "404 decisions" in text
    assert "BUY: 57" in text
    assert "Win rate (closed) | 55.0%" in text
    assert "2026-04-24 → 2026-05-24" in text


def test_package_readme_links_charts_and_cases():
    text = render_package_readme(_stub_headline_stats())
    for path in (
        "charts/verdict-distribution.png",
        "charts/sector-breakdown.png",
        "charts/pnl-distribution.png",
        "charts/score-vs-outcome.png",
        "case-studies/01-best-trade.md",
        "case-studies/04-still-open.md",
        "data/decisions.csv",
        "data/schema.sql",
    ):
        assert path in text, f"missing link: {path}"


def test_data_readme_renders_counts():
    text = render_data_readme(
        as_of="2026-05-24",
        since_days=30,
        n_decisions=404,
        n_positions=45,
        n_summary=4,
    )
    assert "404" in text
    assert "45" in text
    assert "subscribers" in text  # explained in the "excluded" section
```

- [ ] **Step 4: Run tests, verify they fail**

Run: `pytest tests/test_snapshot/test_render.py -v`
Expected: errors — `ModuleNotFoundError: No module named 'app.services.snapshot.render'`

- [ ] **Step 5: Implement render.py**

```python
# app/services/snapshot/render.py
"""Load and render the snapshot's Jinja2 templates."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import jinja2

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=False,
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,  # surface missing keys instead of silent blanks
)


def render_package_readme(headline_stats: Dict[str, str]) -> str:
    template = _env.get_template("README.md.j2")
    return template.render(**headline_stats)


def render_data_readme(
    *, as_of: str, since_days: int, n_decisions: int, n_positions: int, n_summary: int
) -> str:
    template = _env.get_template("data_README.md.j2")
    return template.render(
        as_of=as_of,
        since_days=since_days,
        n_decisions=n_decisions,
        n_positions=n_positions,
        n_summary=n_summary,
    )
```

- [ ] **Step 6: Run tests, verify they pass**

Run: `pytest tests/test_snapshot/test_render.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add app/services/snapshot/templates/ app/services/snapshot/render.py tests/test_snapshot/test_render.py
git commit -m "feat(snapshot): package and data README templates + renderer"
```

---

## Task 6: Case-study drafter

**Files:**
- Create: `app/services/snapshot/case_studies.py`
- Create: `tests/test_snapshot/test_case_studies.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_snapshot/test_case_studies.py
"""Tests for case-study candidate selection and draft markdown."""
from __future__ import annotations

from pathlib import Path

from app.services.snapshot.case_studies import (
    draft_case_study,
    pick_candidates,
)
from app.services.snapshot.db_export import load_decisions, load_positions


def test_pick_candidates_returns_one_per_slot(snapshot_db: Path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    candidates = pick_candidates(decisions, positions)
    assert set(candidates.keys()) == {"best", "worst", "avoided", "open"}
    # best = NVDA (+14.56%), worst = MSFT (-7.83%) per fixture
    assert candidates["best"]["ticker"] == "NVDA"
    assert candidates["worst"]["ticker"] == "MSFT"


def test_pick_candidates_handles_missing_categories(snapshot_db: Path):
    """If e.g. no AVOID exists in the window, that slot is None — drafter handles it."""
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    # Fixture has AVOIDs, but check the empty-data path explicitly:
    import pandas as pd
    empty = pick_candidates(decisions.iloc[0:0], positions.iloc[0:0])
    for slot in ("best", "worst", "avoided", "open"):
        assert empty[slot] is None


def test_draft_case_study_excludes_llm_text(snapshot_db: Path):
    decisions = load_decisions(snapshot_db, since_days=30, as_of="2026-05-24")
    positions = load_positions(snapshot_db)
    candidates = pick_candidates(decisions, positions)
    md = draft_case_study("best", candidates["best"])
    # Must mention structured fields:
    assert "NVDA" in md
    assert "BUY" in md
    # Must NOT mention LLM-prose-only fields (they weren't even loaded, but guard regardless):
    for banned in ("reasoning", "swot", "blindspots", "verification"):
        assert banned not in md.lower(), f"banned token in draft: {banned}"


def test_draft_case_study_handles_none():
    md = draft_case_study("worst", None)
    assert "No candidate" in md
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_snapshot/test_case_studies.py -v`
Expected: errors — `ModuleNotFoundError: No module named 'app.services.snapshot.case_studies'`

- [ ] **Step 3: Implement case_studies.py**

```python
# app/services/snapshot/case_studies.py
"""Pick one candidate per case-study slot and draft a markdown stub.

The drafter intentionally uses only structured columns. Free-text LLM
fields are never accessed — even if they sneak into the input DataFrame
in the future, they won't appear in the draft.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


def _row_to_dict(row: pd.Series, decision_row: Optional[pd.Series]) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "ticker": row.get("ticker"),
        "status": row.get("status"),
        "entry_date": row.get("entry_date"),
        "entry_price": row.get("entry_price"),
        "current_price": row.get("current_price"),
        "exit_date": row.get("exit_date"),
        "exit_price": row.get("exit_price"),
        "realized_pnl_pct": row.get("realized_pnl_pct"),
        "unrealized_pnl_pct": row.get("unrealized_pnl_pct"),
        "exit_reason": row.get("exit_reason"),
    }
    if decision_row is not None:
        d.update({
            "company_name": decision_row.get("company_name"),
            "sector": decision_row.get("sector"),
            "drop_percent": decision_row.get("drop_percent"),
            "recommendation": decision_row.get("recommendation"),
            "ai_score": decision_row.get("ai_score"),
            "deep_research_action": decision_row.get("deep_research_action"),
            "deep_research_score": decision_row.get("deep_research_score"),
            "entry_price_low": decision_row.get("entry_price_low"),
            "entry_price_high": decision_row.get("entry_price_high"),
            "stop_loss": decision_row.get("stop_loss"),
            "take_profit_1": decision_row.get("take_profit_1"),
            "take_profit_2": decision_row.get("take_profit_2"),
        })
    return d


def _join_one(position_row: pd.Series, decisions: pd.DataFrame) -> Dict[str, Any]:
    dp_id = position_row.get("decision_point_id")
    matches = decisions[decisions["id"] == dp_id] if not decisions.empty else pd.DataFrame()
    decision_row = matches.iloc[0] if not matches.empty else None
    return _row_to_dict(position_row, decision_row)


def pick_candidates(
    decisions: pd.DataFrame, positions: pd.DataFrame
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Choose one row for each of: best, worst, avoided, open.

    Returns a dict with keys best/worst/avoided/open; each value is a
    flattened dict of fields suitable for the drafter, or None if no
    candidate exists for that slot.
    """
    out: Dict[str, Optional[Dict[str, Any]]] = {
        "best": None, "worst": None, "avoided": None, "open": None,
    }

    if not positions.empty:
        closed = positions[positions["status"] == "CLOSED"].dropna(subset=["realized_pnl_pct"])
        if not closed.empty:
            best = closed.loc[closed["realized_pnl_pct"].idxmax()]
            worst = closed.loc[closed["realized_pnl_pct"].idxmin()]
            out["best"] = _join_one(best, decisions)
            out["worst"] = _join_one(worst, decisions)

        active = positions[positions["status"] == "ACTIVE"]
        if not active.empty:
            # Pick the one with the highest unrealized P&L, or the most recent entry as tiebreaker.
            if active["unrealized_pnl_pct"].notna().any():
                pick = active.loc[active["unrealized_pnl_pct"].idxmax()]
            else:
                pick = active.iloc[0]
            out["open"] = _join_one(pick, decisions)

    if not decisions.empty:
        avoids = decisions[decisions["recommendation"] == "AVOID"]
        if not avoids.empty:
            # Prefer the one with the biggest drop (most dramatic "knife catch averted")
            pick = avoids.loc[avoids["drop_percent"].idxmin()]  # idxmin: most negative drop
            out["avoided"] = _row_to_dict(
                pd.Series({"ticker": pick.get("symbol"), "status": "N/A"}),
                pick,
            )

    return out


_SLOT_TITLES = {
    "best": "Best trade",
    "worst": "Worst trade",
    "avoided": "Correctly avoided",
    "open": "Still open",
}


def draft_case_study(slot: str, candidate: Optional[Dict[str, Any]]) -> str:
    """Return a markdown draft for the given slot. Hand-edit before commit."""
    title = _SLOT_TITLES.get(slot, slot.title())
    if candidate is None:
        return f"# {title}\n\nNo candidate available for this slot in the current window.\n"

    ticker = candidate.get("ticker") or candidate.get("company_name") or "?"
    company = candidate.get("company_name") or ticker
    sector = candidate.get("sector") or "—"
    drop = candidate.get("drop_percent")
    rec = candidate.get("recommendation") or "—"
    ai_score = candidate.get("ai_score")
    dr_action = candidate.get("deep_research_action") or "—"
    dr_score = candidate.get("deep_research_score")

    entry_low = candidate.get("entry_price_low")
    entry_high = candidate.get("entry_price_high")
    stop = candidate.get("stop_loss")
    tp1 = candidate.get("take_profit_1")
    tp2 = candidate.get("take_profit_2")

    entry_date = candidate.get("entry_date") or "—"
    entry_price = candidate.get("entry_price")
    exit_date = candidate.get("exit_date")
    exit_price = candidate.get("exit_price")
    realized = candidate.get("realized_pnl_pct")
    unrealized = candidate.get("unrealized_pnl_pct")
    cur_price = candidate.get("current_price")
    exit_reason = candidate.get("exit_reason") or "—"

    def fmt(v, suffix=""):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "—"
        if isinstance(v, float):
            return f"{v:.2f}{suffix}"
        return f"{v}{suffix}"

    lines = [
        f"# {title} — {ticker} ({company})",
        "",
        "## The setup",
        f"- **Sector:** {sector}",
        f"- **Drop that triggered the screener:** {fmt(drop, '%')}",
        "",
        "## The verdict",
        f"- **PM:** {rec} (score {fmt(ai_score)})",
        f"- **Deep Research:** {dr_action} (score {fmt(dr_score)})",
        "",
        "## The plan",
        f"- **Entry range:** {fmt(entry_low)} – {fmt(entry_high)}",
        f"- **Stop loss:** {fmt(stop)}",
        f"- **TP1 / TP2:** {fmt(tp1)} / {fmt(tp2)}",
        "",
        "## What happened",
        f"- **Entry date / price:** {entry_date} @ {fmt(entry_price)}",
    ]
    if exit_date:
        lines += [
            f"- **Exit date / price:** {exit_date} @ {fmt(exit_price)}",
            f"- **Realized P&L:** {fmt(realized, '%')}",
            f"- **Exit reason:** {exit_reason}",
        ]
    else:
        lines += [
            f"- **Current price:** {fmt(cur_price)}",
            f"- **Unrealized P&L:** {fmt(unrealized, '%')}",
            "- **Status:** still open",
        ]
    lines += [
        "",
        "## Takeaway",
        "_<one line on what this case illustrates — fill in before committing>_",
        "",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_snapshot/test_case_studies.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/snapshot/case_studies.py tests/test_snapshot/test_case_studies.py
git commit -m "feat(snapshot): case-study candidate picker and markdown drafter"
```

---

## Task 7: Orchestration script + integration test

**Files:**
- Create: `scripts/build_monthly_snapshot.py`
- Create: `tests/test_snapshot/test_integration.py`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/test_snapshot/test_integration.py
"""End-to-end: run the orchestration script against the fixture DB."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_full_snapshot_against_fixture(snapshot_db: Path, tmp_path):
    out_dir = tmp_path / "2026-05-24-package"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "build_monthly_snapshot.py"),
            "--db", str(snapshot_db),
            "--out", str(out_dir),
            "--as-of", "2026-05-24",
            "--since-days", "30",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"script failed: {result.stderr}"

    # Top-level README + four charts
    assert (out_dir / "README.md").exists()
    for chart in (
        "verdict-distribution.png",
        "sector-breakdown.png",
        "pnl-distribution.png",
        "score-vs-outcome.png",
    ):
        assert (out_dir / "charts" / chart).exists(), f"missing chart: {chart}"

    # Case studies
    for case in (
        "01-best-trade.md",
        "02-worst-trade.md",
        "03-avoided-correctly.md",
        "04-still-open.md",
    ):
        assert (out_dir / "case-studies" / case).exists(), f"missing case: {case}"

    # Data
    for f in ("README.md", "decisions.csv", "positions.csv", "monthly_summary.csv", "schema.sql", "manifest.csv"):
        assert (out_dir / "data" / f).exists(), f"missing data file: {f}"

    # Privacy guardrail: no subscribers anywhere
    for path in out_dir.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert b"subscribers" not in content or "data/README.md" in str(path), (
                f"subscribers leaked into {path}"
            )

    # decisions.csv has no banned columns
    header = (out_dir / "data" / "decisions.csv").read_text().splitlines()[0]
    for banned in ("reasoning", "deep_research_reason", "deep_research_swot"):
        assert banned not in header, f"banned column {banned} in decisions.csv"
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest tests/test_snapshot/test_integration.py -v`
Expected: fails — script doesn't exist yet.

- [ ] **Step 3: Implement the orchestration script**

```python
# scripts/build_monthly_snapshot.py
"""Build a browsable monthly snapshot under docs/performance/<as-of>-package/.

Reads subscribers.db in read-only mode, filters to the last N days,
strips every free-text LLM column, renders charts, drafts case-study
markdowns, and writes a curated landing README.

Usage:
  python scripts/build_monthly_snapshot.py --as-of 2026-05-24
  python scripts/build_monthly_snapshot.py --as-of 2026-05-24 --since-days 30
  python scripts/build_monthly_snapshot.py --as-of 2026-05-24 --db subscribers.db --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from app.services.snapshot.aggregates import (  # noqa: E402
    build_monthly_summary,
    compute_headline_stats,
)
from app.services.snapshot.case_studies import (  # noqa: E402
    draft_case_study,
    pick_candidates,
)
from app.services.snapshot.charts import (  # noqa: E402
    chart_pnl_distribution,
    chart_score_vs_outcome,
    chart_sector_breakdown,
    chart_verdict_distribution,
)
from app.services.snapshot.db_export import (  # noqa: E402
    export_snapshot_data,
    load_decisions,
    load_positions,
)
from app.services.snapshot.render import (  # noqa: E402
    render_data_readme,
    render_package_readme,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_snapshot")


_SLOT_FILES = {
    "best":    "01-best-trade.md",
    "worst":   "02-worst-trade.md",
    "avoided": "03-avoided-correctly.md",
    "open":    "04-still-open.md",
}


def _write_manifest(out_dir: Path, file_rows: dict[str, int]) -> None:
    rows = [{"file": f, "rows": n} for f, n in file_rows.items()]
    df = pd.DataFrame(rows)
    df["generated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    df.to_csv(out_dir / "data" / "manifest.csv", index=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="subscribers.db")
    parser.add_argument(
        "--out",
        default=None,
        help="Output dir (default: docs/performance/<as-of>-package/)",
    )
    parser.add_argument("--as-of", required=True, help="YYYY-MM-DD")
    parser.add_argument("--since-days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    out_dir = Path(args.out) if args.out else (
        REPO_ROOT / "docs" / "performance" / f"{args.as_of}-package"
    )

    logger.info("Building snapshot: db=%s, as_of=%s, since_days=%d, out=%s",
                db_path, args.as_of, args.since_days, out_dir)

    if args.dry_run:
        logger.info("--dry-run: no files will be written")
        return 0

    (out_dir / "charts").mkdir(parents=True, exist_ok=True)
    (out_dir / "case-studies").mkdir(parents=True, exist_ok=True)
    (out_dir / "data").mkdir(parents=True, exist_ok=True)

    # Load + export raw data
    decisions = load_decisions(db_path, since_days=args.since_days, as_of=args.as_of)
    positions = load_positions(db_path)
    export_snapshot_data(db_path, out_dir / "data", since_days=args.since_days, as_of=args.as_of)

    # Aggregates
    summary = build_monthly_summary(decisions, positions)
    summary.to_csv(out_dir / "data" / "monthly_summary.csv", index=False)

    # Charts
    chart_verdict_distribution(decisions, out_dir / "charts" / "verdict-distribution.png")
    chart_sector_breakdown(decisions, out_dir / "charts" / "sector-breakdown.png")
    chart_pnl_distribution(positions, out_dir / "charts" / "pnl-distribution.png")
    chart_score_vs_outcome(decisions, positions, out_dir / "charts" / "score-vs-outcome.png")

    # Case studies
    candidates = pick_candidates(decisions, positions)
    for slot, filename in _SLOT_FILES.items():
        md = draft_case_study(slot, candidates[slot])
        (out_dir / "case-studies" / filename).write_text(md)
        logger.info("draft written: %s (candidate=%s)", filename,
                    candidates[slot]["ticker"] if candidates[slot] else "None")

    # READMEs
    stats = compute_headline_stats(decisions, positions, as_of=args.as_of, since_days=args.since_days)
    (out_dir / "README.md").write_text(render_package_readme(stats))
    (out_dir / "data" / "README.md").write_text(render_data_readme(
        as_of=args.as_of,
        since_days=args.since_days,
        n_decisions=len(decisions),
        n_positions=len(positions),
        n_summary=len(summary),
    ))

    _write_manifest(out_dir, {
        "decisions.csv": len(decisions),
        "positions.csv": len(positions),
        "monthly_summary.csv": len(summary),
    })

    logger.info("done. open: %s/README.md", out_dir)
    logger.info("NEXT: review case-studies/*.md and replace the 'Takeaway' placeholder before committing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run integration test, verify it passes**

Run: `pytest tests/test_snapshot/test_integration.py -v`
Expected: 1 passed.

- [ ] **Step 5: Run the full test suite for the snapshot package**

Run: `pytest tests/test_snapshot/ -v`
Expected: all tests pass (6 + 4 + 5 + 3 + 4 + 1 = 23 passed).

- [ ] **Step 6: Commit**

```bash
git add scripts/build_monthly_snapshot.py tests/test_snapshot/test_integration.py
git commit -m "feat(snapshot): orchestration script with end-to-end integration test"
```

---

## Task 8: Generate the real snapshot + update top-level README

**Files:**
- Generated: `docs/performance/2026-05-24-package/**`
- Modify: `README.md`

- [ ] **Step 1: Dry-run against the real DB**

Run: `python scripts/build_monthly_snapshot.py --as-of 2026-05-24 --dry-run`
Expected: logs the planned operation, writes nothing, exits 0.

- [ ] **Step 2: Generate the snapshot**

Run: `python scripts/build_monthly_snapshot.py --as-of 2026-05-24`
Expected: log line "done. open: .../README.md", followed by "NEXT: review case-studies/*.md...".

- [ ] **Step 3: Inspect the output**

Run: `ls -R docs/performance/2026-05-24-package/`
Expected: directory layout exactly as in the plan header (README.md, charts/, case-studies/, data/).

Run: `head -1 docs/performance/2026-05-24-package/data/decisions.csv`
Expected: no banned column names — confirm header contains only allowlisted columns.

- [ ] **Step 4: Edit the four case studies**

Open each `docs/performance/2026-05-24-package/case-studies/0X-*.md` and replace the `_<one line on what this case illustrates — fill in before committing>_` placeholder with a real, hand-written takeaway. This is the human-in-the-loop step — do not skip.

- [ ] **Step 5: Open the package README in a Markdown preview**

Run: `open docs/performance/2026-05-24-package/README.md` (or render via your editor).
Expected: charts embed, links resolve, headline-stat table populated.

- [ ] **Step 6: Update top-level README**

Add a new section to `README.md` immediately after the "How It Runs" section. Locate the line `---` that follows "trade-report jobs — track every decision's outcome..." and insert this block immediately below it:

```markdown
## 📊 Monthly snapshot

Want to see what the system actually produces?
**[docs/performance/2026-05-24-package/](docs/performance/2026-05-24-package/README.md)** is a curated, browsable snapshot of the last 30 days: headline stats, four charts, four case studies, and the raw structured data behind every decision.

---
```

- [ ] **Step 7: Verify nothing else regressed**

Run: `pytest tests/test_snapshot/ -v`
Expected: 23 passed.

- [ ] **Step 8: Commit the snapshot + README update**

```bash
git add docs/performance/2026-05-24-package README.md
git commit -m "$(cat <<'EOF'
docs(snapshot): publish 2026-05-24 monthly snapshot

Browsable 30-day snapshot under docs/performance/2026-05-24-package/:
headline stats, four PNG charts, four hand-edited case studies, and
trimmed CSV exports (no LLM free-text). Top-level README links to it.

Generated by scripts/build_monthly_snapshot.py.
EOF
)"
```

- [ ] **Step 9: Push and verify on github.com**

Run: `git push`
Then open https://github.com/simjbaumgart/stockdrop/tree/main/docs/performance/2026-05-24-package and confirm: README renders with charts inline, CSVs render as sortable tables, case studies render as clean markdown, links resolve.

---

## Self-review notes

- **Spec coverage:** Every section of the spec maps to a task — §3 layout (Task 8 generation), §5 column policy (Task 2 + tests), §6 charts (Task 4), §7 case studies (Task 6), §8 building blocks (Tasks 2–7 one-to-one), §10 testing (each task includes its tests), §11 risks (allowlist test, subscribers test, case-study "worst" slot reserved by construction), §12 acceptance (Task 8 steps 3–9).
- **Placeholder scan:** No "TBD"/"add error handling"/"similar to". The one intentional placeholder is the `_<...>_` text inside generated case-study drafts — explicitly called out as a human-in-the-loop step in Task 8 Step 4.
- **Type consistency:** Function names verified across tasks — `load_decisions`, `load_positions`, `export_snapshot_data`, `build_monthly_summary`, `compute_headline_stats`, `chart_*`, `render_package_readme`, `render_data_readme`, `pick_candidates`, `draft_case_study` are spelled the same way in every reference (definition, test, importer in `build_monthly_snapshot.py`).
