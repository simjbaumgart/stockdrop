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
    # Repair also fails (returns None) -> still PASS_INSUFFICIENT_DATA.
    with patch.object(svc, "_call_agent", return_value="not valid json at all"), patch(
        "app.services.research_service.repair_json_via_flash", return_value=None
    ):
        decision = svc._run_risk_council_and_decision(state, "-6.40%")
    assert decision.get("action") != "AVOID"
    assert decision.get("aborted_reason") == "fund_manager_failed"


def test_fm_truncated_json_is_repaired():
    """A truncated-but-real FM JSON payload (NIO 2026-05-22: clean through
    risk_reward_ratio, cut mid sell_price_low) should be repaired via the
    Gemini Flash pass instead of falling through to PASS_INSUFFICIENT_DATA."""
    svc = ResearchService()
    state = _make_state("NIO")
    truncated = (
        '{"action": "BUY", "conviction": "MODERATE", '
        '"risk_reward_ratio": 0.4, "sell_price_low'
    )
    repaired = {
        "action": "BUY",
        "conviction": "MODERATE",
        "drop_type": "MACRO_SELLOFF",
        "reason": "Repaired payload.",
        "key_factors": [],
    }
    with patch.object(svc, "_call_agent", return_value=truncated), patch(
        "app.services.research_service.repair_json_via_flash", return_value=repaired
    ) as mock_repair:
        decision = svc._run_risk_council_and_decision(state, "-6.40%")
    mock_repair.assert_called_once()
    assert decision.get("action") == "BUY"
    assert decision.get("aborted_reason") is None


def test_fm_error_stub_skips_repair():
    """Error stubs are transport failures with no content — the repair pass
    must NOT be attempted; the path goes straight to PASS_INSUFFICIENT_DATA."""
    svc = ResearchService()
    state = _make_state()
    with patch.object(
        svc,
        "_call_agent",
        return_value="[Error: Fund Manager exceeded 600s wall-clock budget after 0 retries]",
    ), patch("app.services.research_service.repair_json_via_flash") as mock_repair:
        decision = svc._run_risk_council_and_decision(state, "-6.40%")
    mock_repair.assert_not_called()
    assert decision.get("aborted_reason") == "fund_manager_failed"
