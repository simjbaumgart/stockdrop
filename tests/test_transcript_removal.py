"""After removing the DefeatBeta + Finnhub transcript fallbacks, the
research path must not reference them and must not raise when they are absent."""

import inspect

from app.services import stock_service, research_service, finnhub_service


def test_finnhub_transcript_methods_are_gone():
    assert not hasattr(finnhub_service.FinnhubService, "get_transcript_list"), (
        "get_transcript_list should be removed — Finnhub tier returns 403"
    )
    assert not hasattr(finnhub_service.FinnhubService, "get_transcript_content"), (
        "get_transcript_content should be removed — Finnhub tier returns 403"
    )


def test_stock_service_get_latest_transcript_returns_empty():
    """With fallbacks removed, the method must return an empty, well-formed
    result rather than raising."""
    svc = stock_service.StockService()
    result = svc.get_latest_transcript("AAPL")
    assert result == "" or (
        isinstance(result, dict) and not result.get("text")
    ), f"expected empty transcript, got: {result!r}"


def test_research_service_source_has_no_defeatbeta_refs():
    src = inspect.getsource(research_service)
    assert "DefeatBeta" not in src, "research_service should no longer reference DefeatBeta"
    assert "defeatbeta" not in src, "research_service should no longer reference defeatbeta"
