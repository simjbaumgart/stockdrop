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

    # --- BUY_LIMIT: always triggers, regardless of R/R or conviction ---
    # Every buy-side verdict is routed through Deep Research so limit orders
    # cannot slip past review (recurring AFRM/OSCR gap). R/R no longer gates.

    def test_buy_limit_triggers_high_conviction_high_rr(self):
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "HIGH", "risk_reward_ratio": 1.5}
        assert svc._should_trigger_deep_research(report) is True

    def test_buy_limit_triggers_low_conviction(self):
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "LOW", "risk_reward_ratio": 1.5}
        assert svc._should_trigger_deep_research(report) is True

    def test_buy_limit_triggers_low_rr_afrm_case(self):
        """The recurring gap: AFRM was BUY_LIMIT at R/R 0.7 and never reached DR.
        R/R no longer gates BUY_LIMIT, so this must now trigger."""
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "MEDIUM", "risk_reward_ratio": 0.7}
        assert svc._should_trigger_deep_research(report) is True

    def test_buy_limit_triggers_rr_at_one(self):
        """R/R = 1.0 used to fail the strict > 1.0 gate; now triggers."""
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "HIGH", "risk_reward_ratio": 1.0}
        assert svc._should_trigger_deep_research(report) is True

    def test_buy_limit_triggers_rr_below_one(self):
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "HIGH", "risk_reward_ratio": 0.9}
        assert svc._should_trigger_deep_research(report) is True

    def test_buy_limit_triggers_with_invalid_rr(self):
        """A malformed R/R must no longer block a BUY_LIMIT from DR."""
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "HIGH", "risk_reward_ratio": "N/A"}
        assert svc._should_trigger_deep_research(report) is True

    def test_buy_limit_triggers_with_none_rr(self):
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "conviction": "HIGH", "risk_reward_ratio": None}
        assert svc._should_trigger_deep_research(report) is True

    # --- Other actions: never trigger ---

    def test_avoid_does_not_trigger(self):
        svc = _make_service()
        report = {"recommendation": "AVOID", "conviction": "HIGH", "risk_reward_ratio": 3.0}
        assert svc._should_trigger_deep_research(report) is False

    def test_hold_does_not_trigger(self):
        svc = _make_service()
        report = {"recommendation": "HOLD", "conviction": "HIGH", "risk_reward_ratio": 3.0}
        assert svc._should_trigger_deep_research(report) is False


class TestInitialPositionStatus:
    """The position-lifecycle status assigned at analysis time must mirror the
    DR trigger gate: any verdict routed to DR is parked in 'Pending DR Review'
    so DR completion can promote/demote it. R/R does not gate the status."""

    def test_buy_is_pending(self):
        svc = _make_service()
        report = {"recommendation": "BUY", "risk_reward_ratio": 0.5}
        assert svc._initial_position_status(report) == "Pending DR Review"

    def test_buy_limit_low_rr_is_pending_afrm_case(self):
        """AFRM: BUY_LIMIT at R/R 0.7 must be parked in Pending so the DR
        upgrade isn't discarded by finalize (which can't promote Not Owned)."""
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "risk_reward_ratio": 0.7}
        assert svc._initial_position_status(report) == "Pending DR Review"

    def test_buy_limit_high_rr_is_pending(self):
        svc = _make_service()
        report = {"recommendation": "BUY_LIMIT", "risk_reward_ratio": 2.0}
        assert svc._initial_position_status(report) == "Pending DR Review"

    def test_watch_is_not_owned(self):
        svc = _make_service()
        report = {"recommendation": "WATCH", "risk_reward_ratio": 3.0}
        assert svc._initial_position_status(report) == "Not Owned"

    def test_avoid_is_not_owned(self):
        svc = _make_service()
        report = {"recommendation": "AVOID", "risk_reward_ratio": 3.0}
        assert svc._initial_position_status(report) == "Not Owned"
