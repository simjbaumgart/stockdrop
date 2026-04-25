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


def test_stock_service_get_latest_transcript_returns_dict_shape():
    """Transcript fetch must always return a dict with text/date/warning keys,
    even when the ticker has no transcript or DefeatBeta is unavailable.
    Live network is not required for this test — empty dict is acceptable."""
    svc = stock_service.StockService()
    result = svc.get_latest_transcript("ZZZZNOTAREALTICKER")
    assert isinstance(result, dict), f"expected dict, got {type(result).__name__}"
    assert set(result.keys()) >= {"text", "date", "warning"}, (
        f"expected at least text/date/warning keys, got {list(result.keys())}"
    )
    assert result.get("text") == "", "unknown ticker should yield empty text"


def test_research_service_does_not_directly_import_defeatbeta():
    """research_service should not own the DefeatBeta dependency — that lives
    in stock_service. (research_service.transcript_text comes from raw_data
    which is populated by stock_service.)"""
    src = inspect.getsource(research_service)
    assert "defeatbeta_api" not in src, (
        "research_service must not import defeatbeta_api directly — go through stock_service"
    )
