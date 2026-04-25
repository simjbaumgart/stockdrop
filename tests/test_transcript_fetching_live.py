"""Live network test against DefeatBeta. Skipped in CI; run locally to verify
transcript fetching is healthy. Marked with `live_network` so CI can deselect."""

import pytest

from app.services.stock_service import StockService

pytestmark = pytest.mark.live_network


def test_vrsn_transcript_is_fresh_and_substantive():
    """VRSN had Q1 2026 earnings call on 2026-04-23. DefeatBeta lags ~2 months,
    so we expect at minimum the FY2025 Q4 transcript (report_date 2026-02-05)
    with thousands of characters of content."""
    svc = StockService()
    result = svc.get_latest_transcript("VRSN")

    assert isinstance(result, dict)
    assert result["text"], "VRSN must have non-empty transcript text"
    assert len(result["text"]) > 5000, (
        f"VRSN transcript suspiciously short: {len(result['text'])} chars"
    )
    assert result["date"], "transcript must carry a report_date"
    assert result["date"] >= "2026-02-01", (
        f"VRSN transcript stale: report_date={result['date']}"
    )
