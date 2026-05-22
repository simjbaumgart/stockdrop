"""Shadow-model comparison for the News Agent.

For a fixed number of decision points after the Gemini 3.5 Flash upgrade, the
previous News Agent model runs as a non-blocking shadow call alongside
production. The shadow output is logged for offline comparison and never feeds
the live pipeline. The shadow self-disables once SHADOW_RUN_TARGET completed
pairs exist.
"""
import logging
import time
from typing import Any, Callable, Dict, Optional

from app import database

logger = logging.getLogger(__name__)

# Production News Agent model (the upgrade target).
# NOTE: confirm this id against the live Gemini model list before deploying.
PRODUCTION_NEWS_MODEL = "gemini-3.5-flash-preview"

# Previous News Agent model, kept running in shadow for validation.
SHADOW_NEWS_MODEL = "gemini-3-flash-preview"

# Number of completed (successful) shadow pairs after which shadow disables.
SHADOW_RUN_TARGET = 20


def extract_needs_economics(report_text: Optional[str]) -> bool:
    """True if the report sets the downstream Economics Agent trigger flag."""
    return "NEEDS_ECONOMICS: TRUE" in (report_text or "")


def is_shadow_active() -> bool:
    """True while fewer than SHADOW_RUN_TARGET completed pairs exist."""
    try:
        return database.count_news_shadow_runs() < SHADOW_RUN_TARGET
    except Exception as e:
        logger.warning("news shadow active-check failed, disabling shadow: %s", e)
        return False


def run_shadow_call(call_fn: Callable[..., str], prompt: str) -> Dict[str, Any]:
    """Run the shadow model on the identical prompt.

    `call_fn` must be ResearchService._call_grounded_model, which accepts a
    `metrics_sink` keyword argument (added in the model-swap task). Raises on
    failure; the caller is responsible for catching so the live pipeline is
    unaffected.
    """
    metrics: Dict[str, Any] = {}
    t0 = time.monotonic()
    report = call_fn(
        prompt,
        model_name=SHADOW_NEWS_MODEL,
        agent_context="News Agent (Shadow)",
        metrics_sink=metrics,
    )
    metrics["latency_ms"] = int((time.monotonic() - t0) * 1000)
    return {"report": report, "metrics": metrics}


def build_shadow_record(
    ticker: str,
    date: str,
    production_report: str,
    production_metrics: Dict[str, Any],
    shadow_result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Assemble one comparison row from production + shadow outputs."""
    record: Dict[str, Any] = {
        "symbol": ticker,
        "decision_date": date,
        "production_model": production_metrics.get("model", PRODUCTION_NEWS_MODEL),
        "production_report": production_report,
        "production_tokens_in": production_metrics.get("tokens_in", 0),
        "production_tokens_out": production_metrics.get("tokens_out", 0),
        "production_latency_ms": production_metrics.get("latency_ms", 0),
        "production_needs_economics": extract_needs_economics(production_report),
        "shadow_model": SHADOW_NEWS_MODEL,
        "shadow_report": None,
        "shadow_tokens_in": 0,
        "shadow_tokens_out": 0,
        "shadow_latency_ms": 0,
        "shadow_needs_economics": None,
        "shadow_error": None,
    }
    if shadow_result is None:
        record["shadow_error"] = "shadow call failed or timed out"
        return record
    sm = shadow_result.get("metrics", {})
    sr = shadow_result.get("report")
    record["shadow_report"] = sr
    record["shadow_tokens_in"] = sm.get("tokens_in", 0)
    record["shadow_tokens_out"] = sm.get("tokens_out", 0)
    record["shadow_latency_ms"] = sm.get("latency_ms", 0)
    record["shadow_needs_economics"] = extract_needs_economics(sr)
    return record
