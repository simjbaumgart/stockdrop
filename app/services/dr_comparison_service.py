"""Dual-run challenger service for deep research comparison.

When DR_DUAL_RUN is truthy ("1"/"true"/"yes") this service runs the Claude
challenger alongside the authoritative Gemini DR. Gemini is unaffected — this
module only adds a shadow run that stores results in the dr_comparison table.

Usage (called from deep_research_service.queue_research_task after the Gemini
task is enqueued):

    dr_comparison_service.trigger(decision_id, symbol, context)

trigger() returns immediately (snapshot + thread spawn only). All blocking
work happens in a daemon thread so the asyncio event loop is never blocked.
Any exception inside trigger() or the challenger thread is logged and swallowed.
"""
import logging
import sqlite3
import threading
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class DRComparisonService:
    """Runs the Claude challenger and persists a paired Gemini/Claude record."""

    def trigger(
        self,
        decision_id: int,
        symbol: str,
        context: dict,
    ) -> None:
        """Snapshot PM baseline and spawn the challenger thread.

        Returns immediately. Never raises — all exceptions are logged and
        swallowed so the live Gemini path is never affected.
        """
        try:
            from app.database import snapshot_pm_baseline, create_dr_comparison

            pm_baseline = snapshot_pm_baseline(decision_id)
            run_date = datetime.utcnow().strftime("%Y-%m-%d")
            comparison_id = create_dr_comparison(decision_id, symbol, run_date, pm_baseline)
            if comparison_id < 0:
                logger.error(
                    "[Dual-Run] create_dr_comparison returned -1 for decision_id=%s symbol=%s — skipping challenger",
                    decision_id, symbol,
                )
                return

            t = threading.Thread(
                target=self._run_challenger,
                args=(comparison_id, decision_id, symbol, context),
                daemon=True,
            )
            t.start()
            logger.info(
                "[Dual-Run] challenger thread spawned for decision_id=%s symbol=%s comparison_id=%s",
                decision_id, symbol, comparison_id,
            )
        except Exception as e:
            logger.error(
                "[Dual-Run] trigger() failed (live path unaffected): decision_id=%s symbol=%s error=%s",
                decision_id, symbol, e,
            )

    def _run_challenger(
        self,
        comparison_id: int,
        decision_id: int,
        symbol: str,
        context: dict,
    ) -> None:
        """Run the Claude challenger, update cl_*, wait for Gemini, finalize.

        Runs in a daemon thread — never propagates exceptions.
        """
        from app.database import (
            set_dr_comparison_status,
            update_dr_comparison_claude,
            finalize_dr_comparison,
        )
        from app.services.token_pricing import compute_cost, CLAUDE_WEB_SEARCH_USD_PER_1K

        try:
            # Lazy import to avoid circular-import at module load time.
            from app.services.claude_deep_research_service import claude_deep_research_service

            result = claude_deep_research_service.execute_deep_research(
                symbol, context, decision_id
            )

            if result is None:
                logger.warning(
                    "[Dual-Run] Claude returned None for decision_id=%s symbol=%s — marking FAILED",
                    decision_id, symbol,
                )
                set_dr_comparison_status(comparison_id, "FAILED")
                return

            meta = result.get("_claude_research_meta", {})
            usage = meta.get("usage", {})

            raw_cost = compute_cost(
                "claude-opus-4-8",
                usage.get("in", 0),
                usage.get("out", 0),
            )
            search_cost = (meta.get("search_count", 0) / 1000.0) * CLAUDE_WEB_SEARCH_USD_PER_1K
            total_cost = (raw_cost or 0.0) + search_cost

            cl_meta = {
                "search_count": meta.get("search_count"),
                "source_count": len(meta.get("source_urls", [])),
                "cost_usd": round(total_cost, 4),
                "latency_s": meta.get("latency_s"),
            }

            update_dr_comparison_claude(comparison_id, result, cl_meta)
            logger.info(
                "[Dual-Run] Claude result stored for comparison_id=%s; waiting for Gemini DR",
                comparison_id,
            )

            self._wait_for_gemini(decision_id)
            finalize_dr_comparison(comparison_id)
            logger.info(
                "[Dual-Run] comparison_id=%s FINALIZED",
                comparison_id,
            )

        except Exception as e:
            logger.error(
                "[Dual-Run] _run_challenger failed for comparison_id=%s decision_id=%s: %s",
                comparison_id, decision_id, e,
            )
            try:
                from app.database import set_dr_comparison_status
                set_dr_comparison_status(comparison_id, "FAILED")
            except Exception as inner:
                logger.error(
                    "[Dual-Run] could not mark FAILED for comparison_id=%s: %s",
                    comparison_id, inner,
                )

    def _wait_for_gemini(
        self,
        decision_id: int,
        timeout_s: int = 600,
        poll_s: int = 15,
    ) -> None:
        """Poll decision_points.deep_research_review_verdict until non-null/non-empty
        or timeout_s is reached. Uses time.sleep — this is a background thread only.
        """
        from app.database import DB_NAME

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                conn = sqlite3.connect(DB_NAME)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT deep_research_review_verdict FROM decision_points WHERE id = ?",
                    (decision_id,),
                )
                row = cur.fetchone()
                conn.close()
                if row is not None and row["deep_research_review_verdict"]:
                    return
            except Exception as e:
                logger.warning(
                    "[Dual-Run] _wait_for_gemini poll error decision_id=%s: %s",
                    decision_id, e,
                )
            time.sleep(poll_s)

        logger.warning(
            "[Dual-Run] _wait_for_gemini timed out after %ss for decision_id=%s — finalizing anyway",
            timeout_s, decision_id,
        )


dr_comparison_service = DRComparisonService()
