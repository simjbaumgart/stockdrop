"""
Tests for Task 3 of pipeline-error-hardening: parse-failure fallback must
preserve the PM verdict instead of silently downgrading to AVOID.

Regression: 04-22 ADBE had PM produce BUY_LIMIT, Deep Research Flash-repair
timed out, and the raw fallback overrode everything to AVOID. Correct
behaviour is `review_verdict == 'PENDING_REVIEW'` + `action is None`, so
the PM verdict is preserved upstream and the row can be re-queued.
"""

from unittest.mock import patch

from app.services.deep_research_service import DeepResearchService


def test_parse_failure_returns_pending_review_not_avoid():
    """When Flash repair returns None, _parse_output must not silently
    downgrade the verdict to AVOID. It should return a PENDING_REVIEW
    sentinel with action=None so the PM verdict is preserved upstream."""
    svc = DeepResearchService.__new__(DeepResearchService)  # bypass __init__
    svc.api_key = "fake"

    poll_data = {
        "outputs": [
            {"text": "not valid json and not repairable either"}
        ]
    }

    with patch.object(svc, "_repair_json_using_flash", return_value=None):
        result = svc._parse_output(poll_data, schema_type="individual")

    assert result is not None
    assert result["review_verdict"] == "PENDING_REVIEW", (
        f"Expected PENDING_REVIEW, got {result['review_verdict']!r}"
    )
    assert result["action"] is None, (
        "action must be None so PM verdict is preserved"
    )
    assert "raw_report_full" in result


def test_parse_failure_fallback_preserves_result_schema_keys():
    """The fallback dict must still contain the keys downstream code
    reads (swot_analysis, verification_results, etc.) so _handle_completion
    doesn't KeyError."""
    svc = DeepResearchService.__new__(DeepResearchService)
    svc.api_key = "fake"

    poll_data = {"outputs": [{"text": "unparseable garbage"}]}

    with patch.object(svc, "_repair_json_using_flash", return_value=None):
        result = svc._parse_output(poll_data, schema_type="individual")

    # These keys are all read by _handle_completion / _apply_trading_level_overrides
    expected_keys = {
        "review_verdict", "action", "conviction", "drop_type", "risk_level",
        "catalyst_type", "entry_price_low", "entry_price_high", "stop_loss",
        "take_profit_1", "take_profit_2", "upside_percent",
        "downside_risk_percent", "risk_reward_ratio", "pre_drop_price",
        "entry_trigger", "reassess_in_days", "global_market_analysis",
        "local_market_analysis", "swot_analysis", "verification_results",
        "council_blindspots", "knife_catch_warning", "reason",
        "raw_report_full",
    }
    missing = expected_keys - set(result.keys())
    assert not missing, f"Fallback dict dropped keys: {missing}"


def test_handle_completion_skips_trading_overrides_when_action_none():
    """When the parse fallback is hit (action=None), _handle_completion
    must NOT call _apply_trading_level_overrides — otherwise a bogus
    PENDING_REVIEW row would wipe the entry/stop/tp columns the PM set."""
    svc = DeepResearchService.__new__(DeepResearchService)
    svc.api_key = "fake"

    pending_result = {
        "review_verdict": "PENDING_REVIEW",
        "action": None,
        "conviction": "LOW",
        "risk_level": "Unknown",
        "catalyst_type": "Parse Error",
        "knife_catch_warning": False,
        "swot_analysis": {},
        "verification_results": [],
        "council_blindspots": [],
        "reason": "parse failure",
        "global_market_analysis": "",
        "local_market_analysis": "",
    }

    task = {"symbol": "ADBE", "decision_id": 42}

    with patch(
        "app.database.update_deep_research_data",
        return_value=True,
    ), patch.object(
        svc, "_apply_trading_level_overrides"
    ) as mock_apply, patch.object(
        svc, "_print_deep_research_result"
    ), patch.object(
        svc, "_save_result_to_file"
    ):
        svc._handle_completion(task, pending_result)

    assert not mock_apply.called, (
        "PENDING_REVIEW (action=None) must NOT trigger trading-level overrides"
    )


def test_handle_completion_writes_pending_review_to_verdict_col_when_action_none():
    """When action is None, verdict_for_db should be 'PENDING_REVIEW'
    (not None, which could violate NOT NULL constraints / muddle reports)."""
    svc = DeepResearchService.__new__(DeepResearchService)
    svc.api_key = "fake"

    pending_result = {
        "review_verdict": "PENDING_REVIEW",
        "action": None,
        "conviction": "LOW",
        "risk_level": "Unknown",
        "catalyst_type": "Parse Error",
        "knife_catch_warning": False,
        "swot_analysis": {},
        "verification_results": [],
        "council_blindspots": [],
        "reason": "parse failure",
        "global_market_analysis": "",
        "local_market_analysis": "",
    }

    task = {"symbol": "ADBE", "decision_id": 42}

    captured = {}

    def fake_update(**kwargs):
        captured.update(kwargs)
        return True

    with patch(
        "app.database.update_deep_research_data",
        side_effect=fake_update,
    ), patch.object(
        svc, "_apply_trading_level_overrides"
    ), patch.object(
        svc, "_print_deep_research_result"
    ), patch.object(
        svc, "_save_result_to_file"
    ):
        svc._handle_completion(task, pending_result)

    assert captured.get("verdict") == "PENDING_REVIEW", (
        f"Expected verdict_for_db='PENDING_REVIEW', got {captured.get('verdict')!r}"
    )
    assert captured.get("action") is None
    assert captured.get("review_verdict") == "PENDING_REVIEW"


# ---------------------------------------------------------------------------
# Regression: PENDING_REVIEW rows must be treated as "needs re-review", not
# "deep research complete". Three call sites historically leaked these rows:
#
#   1. app/services/stock_service.py::_process_deep_research_backfill
#   2. app/database.py::get_unbatched_candidates_by_date
#   3. app/database.py::get_distinct_dates_with_unbatched_candidates
#
# Drive the real public functions against an isolated sqlite file so the
# SQL filter is actually exercised end-to-end.
# ---------------------------------------------------------------------------

import os
import sqlite3
import importlib

import pytest


def _seed_decision_points(db_path: str, rows):
    """Create a minimal decision_points table and insert the given rows.

    Each row is a dict with keys: symbol, timestamp, recommendation,
    conviction, risk_reward_ratio, deep_research_verdict, batch_id,
    deep_research_score. Only the columns the queries under test actually
    read are required; the rest are nullable.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE decision_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            price_at_decision REAL NOT NULL DEFAULT 0,
            drop_percent REAL NOT NULL DEFAULT 0,
            recommendation TEXT NOT NULL,
            reasoning TEXT,
            status TEXT,
            timestamp TIMESTAMP,
            conviction TEXT,
            risk_reward_ratio REAL,
            deep_research_verdict TEXT,
            deep_research_score INTEGER,
            batch_id TEXT
        )
        """
    )
    for r in rows:
        cur.execute(
            """
            INSERT INTO decision_points
              (symbol, price_at_decision, drop_percent, recommendation,
               timestamp, conviction, risk_reward_ratio,
               deep_research_verdict, deep_research_score, batch_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["symbol"],
                r.get("price_at_decision", 100.0),
                r.get("drop_percent", -6.0),
                r["recommendation"],
                r["timestamp"],
                r.get("conviction"),
                r.get("risk_reward_ratio"),
                r.get("deep_research_verdict"),
                r.get("deep_research_score"),
                r.get("batch_id"),
            ),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point app.database at a throwaway sqlite file and return its path."""
    db_path = str(tmp_path / "test_pending_review.db")
    # Database module caches DB_NAME at import time from DB_PATH env var.
    monkeypatch.setenv("DB_PATH", db_path)
    import app.database as dbmod
    importlib.reload(dbmod)
    yield db_path, dbmod
    # Reload again after test with default env so other tests see the real
    # module state.
    monkeypatch.delenv("DB_PATH", raising=False)
    importlib.reload(dbmod)


def test_get_unbatched_candidates_excludes_pending_review(isolated_db):
    """Rows with deep_research_verdict='PENDING_REVIEW' must NOT be
    returned as batching candidates — they still need re-review."""
    db_path, dbmod = isolated_db
    date_str = "2026-04-22"
    _seed_decision_points(
        db_path,
        [
            {
                "symbol": "BUYROW",
                "timestamp": f"{date_str} 10:00:00",
                "recommendation": "BUY_LIMIT",
                "conviction": "MODERATE",
                "risk_reward_ratio": 2.0,
                "deep_research_verdict": "BUY_LIMIT",
                "deep_research_score": 8,
                "batch_id": None,
            },
            {
                "symbol": "PENDROW",
                "timestamp": f"{date_str} 11:00:00",
                "recommendation": "BUY_LIMIT",
                "conviction": "MODERATE",
                "risk_reward_ratio": 2.0,
                "deep_research_verdict": "PENDING_REVIEW",
                "deep_research_score": None,
                "batch_id": None,
            },
        ],
    )

    results = dbmod.get_unbatched_candidates_by_date(date_str)
    symbols = {r["symbol"] for r in results}

    assert "BUYROW" in symbols, "fully-parsed BUY_LIMIT row should be a batch candidate"
    assert "PENDROW" not in symbols, (
        "PENDING_REVIEW row must not be batched as if complete"
    )


def test_get_distinct_dates_excludes_pending_review_only_days(isolated_db):
    """A date that has ONLY PENDING_REVIEW rows must not be reported as
    having unbatched candidates ready for batching."""
    db_path, dbmod = isolated_db
    _seed_decision_points(
        db_path,
        [
            {
                "symbol": "PEND1",
                "timestamp": "2026-04-20 10:00:00",
                "recommendation": "BUY_LIMIT",
                "deep_research_verdict": "PENDING_REVIEW",
                "batch_id": None,
            },
            {
                "symbol": "OK1",
                "timestamp": "2026-04-21 10:00:00",
                "recommendation": "BUY_LIMIT",
                "deep_research_verdict": "BUY_LIMIT",
                "batch_id": None,
            },
        ],
    )

    dates = dbmod.get_distinct_dates_with_unbatched_candidates()

    assert "2026-04-21" in dates
    assert "2026-04-20" not in dates, (
        "dates that only have PENDING_REVIEW rows must not be offered for batching"
    )


def test_stock_service_backfill_query_includes_pending_review():
    """stock_service._process_deep_research_backfill's SQL must select
    PENDING_REVIEW rows (so they are re-run), while excluding fully
    completed BUY_LIMIT verdicts."""
    # Drive the exact SQL fragment from the function against an in-memory
    # sqlite. The function itself is tightly coupled to real Deep Research
    # queues; a narrower SQL-level regression is the pragmatic check here.
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE decision_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp TIMESTAMP,
            recommendation TEXT,
            conviction TEXT,
            risk_reward_ratio REAL,
            deep_research_verdict TEXT
        )
        """
    )
    rows = [
        ("DONE", "2026-04-22 10:00:00", "BUY_LIMIT", "MODERATE", 2.0, "BUY_LIMIT"),
        ("PEND", "2026-04-22 11:00:00", "BUY_LIMIT", "MODERATE", 2.0, "PENDING_REVIEW"),
        ("ERR",  "2026-04-22 12:00:00", "BUY_LIMIT", "MODERATE", 2.0, "ERROR_PARSING"),
        ("NULL", "2026-04-22 13:00:00", "BUY_LIMIT", "MODERATE", 2.0, None),
        ("LOWR", "2026-04-22 14:00:00", "BUY_LIMIT", "MODERATE", 1.0, None),  # r/r too low
        ("WEAK", "2026-04-22 15:00:00", "BUY_LIMIT", "LOW",      2.0, None),  # conviction too low
    ]
    cur.executemany(
        "INSERT INTO decision_points (symbol, timestamp, recommendation, "
        "conviction, risk_reward_ratio, deep_research_verdict) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()

    # Exact SQL used in app/services/stock_service.py::_process_deep_research_backfill
    query = """
        SELECT symbol FROM decision_points
        WHERE date(timestamp) = ?
        AND recommendation IN ('BUY', 'BUY_LIMIT')
        AND conviction IN ('MODERATE', 'HIGH')
        AND risk_reward_ratio >= 1.5
        AND (deep_research_verdict IS NULL OR deep_research_verdict = '' OR deep_research_verdict = '-' OR deep_research_verdict LIKE 'UNKNOWN%' OR deep_research_verdict = 'ERROR_PARSING' OR deep_research_verdict = 'PENDING_REVIEW')
    """
    cur.execute(query, ("2026-04-22",))
    got = {r[0] for r in cur.fetchall()}
    conn.close()

    assert "PEND" in got, "PENDING_REVIEW must be re-queued for deep research"
    assert "ERR" in got
    assert "NULL" in got
    assert "DONE" not in got, "fully-parsed BUY_LIMIT rows must not be re-queued"
    assert "LOWR" not in got
    assert "WEAK" not in got


def test_get_unbatched_candidates_excludes_legacy_parse_failures(isolated_db):
    """Legacy ERROR_PARSING and UNKNOWN-prefix verdicts must also be
    excluded from batching — same intent as PENDING_REVIEW, they are
    parse failures that the backfill will re-run."""
    db_path, dbmod = isolated_db
    date_str = "2026-04-22"
    _seed_decision_points(
        db_path,
        [
            {
                "symbol": "ERRROW",
                "timestamp": f"{date_str} 10:00:00",
                "recommendation": "BUY_LIMIT",
                "deep_research_verdict": "ERROR_PARSING",
                "batch_id": None,
            },
            {
                "symbol": "UNKROW",
                "timestamp": f"{date_str} 11:00:00",
                "recommendation": "BUY_LIMIT",
                "deep_research_verdict": "UNKNOWN (Parse Error)",
                "batch_id": None,
            },
            {
                "symbol": "OKROW",
                "timestamp": f"{date_str} 12:00:00",
                "recommendation": "BUY_LIMIT",
                "deep_research_verdict": "BUY_LIMIT",
                "batch_id": None,
            },
        ],
    )

    symbols = {r["symbol"] for r in dbmod.get_unbatched_candidates_by_date(date_str)}
    assert "OKROW" in symbols
    assert "ERRROW" not in symbols, "ERROR_PARSING rows must not be batched as complete"
    assert "UNKROW" not in symbols, "UNKNOWN-prefix rows must not be batched as complete"


def test_get_distinct_dates_excludes_legacy_parse_failure_only_days(isolated_db):
    """A date populated only with ERROR_PARSING / UNKNOWN rows must not
    appear as a batchable date."""
    db_path, dbmod = isolated_db
    _seed_decision_points(
        db_path,
        [
            {
                "symbol": "ERR1",
                "timestamp": "2026-04-19 10:00:00",
                "recommendation": "BUY_LIMIT",
                "deep_research_verdict": "ERROR_PARSING",
                "batch_id": None,
            },
            {
                "symbol": "UNK1",
                "timestamp": "2026-04-20 10:00:00",
                "recommendation": "BUY_LIMIT",
                "deep_research_verdict": "UNKNOWN (Parse Error)",
                "batch_id": None,
            },
            {
                "symbol": "OK1",
                "timestamp": "2026-04-21 10:00:00",
                "recommendation": "BUY_LIMIT",
                "deep_research_verdict": "BUY_LIMIT",
                "batch_id": None,
            },
        ],
    )

    dates = dbmod.get_distinct_dates_with_unbatched_candidates()
    assert "2026-04-21" in dates
    assert "2026-04-19" not in dates
    assert "2026-04-20" not in dates
