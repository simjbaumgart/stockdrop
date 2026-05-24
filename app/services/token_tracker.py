# app/services/token_tracker.py
"""
Persists one row per Gemini API call to `agent_token_usage` and rolls
up per-decision totals onto `decision_points`.

Thread-safety: every call opens its own short-lived sqlite3 connection
and closes it after a single INSERT (or UPDATE for the rollup). WAL
mode (enabled in init_db) serializes writes via the WAL file rather
than locking the whole DB, so concurrent calls from the 5-sensor and
3-debate ThreadPoolExecutors land cleanly.

Note on DB_NAME lookup: we deliberately reference `app.database.DB_NAME`
via the module (not `from app.database import DB_NAME`) so that test
fixtures can `monkeypatch.setattr(app.database, "DB_NAME", tmp_path)`
without needing a module reload.
"""
import logging
import sqlite3

import app.database as _db
from app.services.token_pricing import compute_cost

logger = logging.getLogger(__name__)


def record_llm_call(
    *,
    decision_id: int,
    ticker: str,
    run_date: str,
    stage: str,
    agent_name: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """Insert one row into agent_token_usage. Failures are logged, not raised —
    cost tracking must never break the live pipeline.
    """
    try:
        cost = compute_cost(model, tokens_in, tokens_out)
        conn = sqlite3.connect(_db.DB_NAME)
        try:
            conn.execute(
                """
                INSERT INTO agent_token_usage
                  (decision_id, ticker, run_date, stage, agent_name,
                   model, tokens_in, tokens_out, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (decision_id, ticker, run_date, stage, agent_name,
                 model, tokens_in, tokens_out, cost),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(
            "record_llm_call failed for %s/%s (%s): %s",
            ticker, agent_name, model, e,
        )


def rollup_decision_totals(decision_id: int) -> None:
    """Recompute the four denormalized total_* columns on decision_points
    from agent_token_usage. Idempotent — safe to re-run.
    """
    try:
        conn = sqlite3.connect(_db.DB_NAME)
        try:
            conn.execute(
                """
                UPDATE decision_points
                SET total_tokens_in   = (SELECT COALESCE(SUM(tokens_in), 0)
                                         FROM agent_token_usage WHERE decision_id = ?),
                    total_tokens_out  = (SELECT COALESCE(SUM(tokens_out), 0)
                                         FROM agent_token_usage WHERE decision_id = ?),
                    total_cost_usd    = (SELECT SUM(cost_usd)
                                         FROM agent_token_usage WHERE decision_id = ?),
                    total_llm_calls   = (SELECT COUNT(*)
                                         FROM agent_token_usage WHERE decision_id = ?)
                WHERE id = ?
                """,
                (decision_id, decision_id, decision_id, decision_id, decision_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("rollup_decision_totals failed for decision_id=%s: %s",
                       decision_id, e)
