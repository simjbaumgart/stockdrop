"""
Gemini pricing for token cost computation.

UNVERIFIED — values below MUST be confirmed against Google's current
Gemini 3 rate card before the cost numbers in agent_token_usage can
be trusted. Do NOT copy stale values from
scripts/analysis/news_shadow_report.py.

Unit convention: USD per 1,000,000 tokens (matches Google's published
format).
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# USD per 1M tokens.
# All values are placeholders. Fill in real rates before relying on cost.
GEMINI_PRICING = {
    "gemini-3-pro-preview":     {"in": 0.0, "out": 0.0},  # TODO verify
    "gemini-3.1-pro-preview":   {"in": 0.0, "out": 0.0},  # TODO verify
    "gemini-3-flash-preview":   {"in": 0.0, "out": 0.0},  # TODO verify
    "gemini-3.5-flash-preview": {"in": 0.0, "out": 0.0},  # TODO verify (news shadow prod model)
    "deep-research-pro":        {"in": 0.0, "out": 0.0},  # TODO verify (if separately priced)
}


def compute_cost(model: str, tokens_in: int, tokens_out: int) -> Optional[float]:
    """Return cost in USD, or None if the model is not in the pricing table.

    None makes the gap visible in SUM(cost_usd) and forces the table to
    be filled in. 0.0 means "model is known and the placeholder rates are
    still in place" — also visible, but separately, in COUNT WHERE cost_usd = 0.
    """
    rates = GEMINI_PRICING.get(model)
    if rates is None:
        logger.warning("token_pricing: unknown model %r — cost_usd will be NULL", model)
        return None
    return (tokens_in / 1_000_000) * rates["in"] + (tokens_out / 1_000_000) * rates["out"]
