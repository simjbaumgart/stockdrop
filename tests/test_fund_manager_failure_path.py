"""Verify that a Fund Manager error stub or unparseable JSON produces a
PASS_INSUFFICIENT_DATA response, not a phantom AVOID/LOW row."""

import os
import sys

os.environ.setdefault("DB_PATH", "test_fm_failure.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

import pytest

from app.services.research_service import ResearchService
from app.models.market_state import MarketState


def _make_state(ticker="LSTR"):
    state = MarketState(ticker=ticker, date="2026-05-14")
    # Pre-populate Phase 1 + Phase 2 reports so the gate passes.
    long_blob = "x" * 400
    state.reports = {
        "technical": long_blob,
        "news": long_blob,
        "market_sentiment": long_blob,
        "competitive": long_blob,
        "seeking_alpha": long_blob,
        "bull": long_blob,
        "bear": long_blob,
        "risk": long_blob,
    }
    return state


def test_fm_error_stub_returns_insufficient_data():
    svc = ResearchService()
    state = _make_state()
    with patch.object(
        svc,
        "_call_agent",
        return_value="[Error: Fund Manager exceeded 600s wall-clock budget after 0 retries]",
    ):
        decision = svc._run_risk_council_and_decision(state, "-6.40%")
    assert decision.get("action") != "AVOID", "must not silently default to AVOID"
    assert decision.get("aborted_reason") == "fund_manager_failed", decision


def test_fm_unparseable_json_returns_insufficient_data():
    svc = ResearchService()
    state = _make_state()
    with patch.object(svc, "_call_agent", return_value="not valid json at all"):
        decision = svc._run_risk_council_and_decision(state, "-6.40%")
    assert decision.get("action") != "AVOID"
    assert decision.get("aborted_reason") == "fund_manager_failed"
