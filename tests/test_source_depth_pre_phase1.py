# tests/test_source_depth_pre_phase1.py
"""v0.8.2-288 review #4 (ORCL/PD): a thin-coverage preferred-share ticker
burned 5 Phase 1 agent calls before the source-depth gate fired. The gate
must run BEFORE any agent dispatch, and the screener must drop
preferred-share notation (symbol contains '/') outright."""

from types import SimpleNamespace

import pytest

import app.services.research_service as rs
from app.services.tradingview_service import exclude_non_common_tickers


def test_source_depth_aborts_before_any_agent_call(monkeypatch):
    svc = rs.ResearchService.__new__(rs.ResearchService)
    monkeypatch.setattr(svc, "_check_and_increment_usage", lambda: True)
    monkeypatch.setattr(
        rs.gatekeeper_service, "check_market_regime", lambda: None
    )

    def _boom(*a, **k):
        raise AssertionError("agent dispatched despite thin sources")

    monkeypatch.setattr(svc, "_call_agent", _boom)
    monkeypatch.setattr(svc, "_run_market_sentiment_cached", _boom)

    thin = {
        "change_percent": -6.0,
        "news_items": [],
        "seeking_alpha_local_counts": {"analysis": 0, "news": 0, "press_releases": 0},
        "indicators": {},
    }
    result = svc.analyze_stock("ORCLPD", thin, decision_id=None)
    assert result["recommendation"] == "PASS_INSUFFICIENT_DATA"
    assert result["aborted_reason"] == "insufficient_source_depth"


def test_exclude_non_common_tickers():
    movers = [
        {"symbol": "ORCL", "change_percent": -5.0},
        {"symbol": "ORCL/PD", "change_percent": -6.0},   # preferred series D
        {"symbol": "BRK.B", "change_percent": -5.5},     # share class dot is fine
    ]
    kept = exclude_non_common_tickers(movers)
    assert [m["symbol"] for m in kept] == ["ORCL", "BRK.B"]
