"""Tests for _should_trigger_deep_research BUY_LIMIT logic."""
import pytest
from unittest.mock import MagicMock


def _make_service():
    """Create a minimal StockService instance for testing the trigger method."""
    from app.services.stock_service import StockService
    svc = StockService.__new__(StockService)
    return svc


class TestShouldTriggerDeepResearch:
    """Tests for _should_trigger_deep_research method."""

    # --- BUY: always triggers (unchanged) ---

    def test_buy_always_triggers(self):
        svc = _make_service()
        report = {"recommendation": "BUY", "conviction": "LOW", "risk_reward_ratio": 0.5}
        assert svc._should_trigger_deep_research(report) is True

    # --- BUY_LIMIT: triggers when R/R > 1.0, conviction irrelevant ---
    # (Threshold lowered from 1.25 to 1.0 so the Pending DR Review gate in
    # stock_service._run_deep_analysis cannot strand rows.)

    def test_buy_limit_triggers_high_conviction_rr_above_threshold(self):
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "HIGH", "risk_reward_ratio": 1.5}
        assert svc._should_trigger_deep_research(report) is True

    def test_buy_limit_triggers_low_conviction_rr_above_threshold(self):
        """Key new behavior: LOW conviction with R/R > 1.0 should now trigger."""
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "LOW", "risk_reward_ratio": 1.5}
        assert svc._should_trigger_deep_research(report) is True

    def test_buy_limit_triggers_medium_conviction_rr_above_threshold(self):
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "MEDIUM", "risk_reward_ratio": 1.3}
        assert svc._should_trigger_deep_research(report) is True

    def test_buy_limit_triggers_at_old_threshold(self):
        """R/R = 1.25 now triggers (used to be at-threshold and ignored)."""
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "HIGH", "risk_reward_ratio": 1.25}
        assert svc._should_trigger_deep_research(report) is True

    def test_buy_limit_does_not_trigger_rr_at_threshold(self):
        """R/R must be strictly greater than 1.0."""
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "HIGH", "risk_reward_ratio": 1.0}
        assert svc._should_trigger_deep_research(report) is False

    def test_buy_limit_does_not_trigger_rr_below_threshold(self):
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "HIGH", "risk_reward_ratio": 0.9}
        assert svc._should_trigger_deep_research(report) is False

    def test_buy_limit_handles_string_rr(self):
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "LOW", "risk_reward_ratio": "1.5"}
        assert svc._should_trigger_deep_research(report) is True

    def test_buy_limit_handles_invalid_rr(self):
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "HIGH", "risk_reward_ratio": "N/A"}
        assert svc._should_trigger_deep_research(report) is False

    def test_buy_limit_handles_none_rr(self):
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "HIGH", "risk_reward_ratio": None}
        assert svc._should_trigger_deep_research(report) is False

    # --- Other actions: never trigger ---

    def test_avoid_does_not_trigger(self):
        svc = _make_service()
        report = {"recommendation": "AVOID", "conviction": "HIGH", "risk_reward_ratio": 3.0}
        assert svc._should_trigger_deep_research(report) is False

    def test_hold_does_not_trigger(self):
        svc = _make_service()
        report = {"recommendation": "HOLD", "conviction": "HIGH", "risk_reward_ratio": 3.0}
        assert svc._should_trigger_deep_research(report) is False
