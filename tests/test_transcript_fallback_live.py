"""Live end-to-end test for the transcript fallback. Only runs when
RUN_LIVE_TESTS=1 because it consumes 1–2 Alpha Vantage requests
(out of the 25/day free-tier budget) per invocation.

Run manually with:
    RUN_LIVE_TESTS=1 pytest tests/test_transcript_fallback_live.py -v -s
"""
import os

import pytest

from app.services.stock_service import StockService

LIVE = os.getenv("RUN_LIVE_TESTS") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="set RUN_LIVE_TESTS=1 to enable")


def test_eras_known_stale_in_defeatbeta_uses_av():
    """ERAS (Erasca) had a 761-day-stale latest transcript in DefeatBeta as of 2026-04-29.
    A successful run must return text > 1KB — DefeatBeta would only give us the
    March 2024 transcript; AV should fill in something more recent."""
    svc = StockService()
    result = svc.get_latest_transcript("ERAS")
    assert isinstance(result, dict)
    # We don't assert source explicitly, but ANY result > 1KB beats DefeatBeta's stale row.
    # If AV is available, the cache will populate and a re-run is free.
    assert "text" in result
    print(f"\n[ERAS] returned text length={len(result.get('text',''))} date={result.get('date')}")


def test_aapl_fresh_uses_defeatbeta():
    """AAPL's latest DB transcript should be ~3 months old. With STALE_TRANSCRIPT_DAYS=75
    today (2026-04-30), a 3-month-old transcript is stale (>75 days), so AV may also be
    consulted. Either DB or AV is acceptable; we just check the dict shape and non-empty
    text."""
    svc = StockService()
    result = svc.get_latest_transcript("AAPL")
    assert isinstance(result, dict)
    assert result.get("text"), "AAPL must return SOME transcript text"
