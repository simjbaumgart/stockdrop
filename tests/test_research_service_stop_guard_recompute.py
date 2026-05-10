"""When the deterministic stop-guard widens the stop, the response dict
must carry the recomputed downside_risk_percent and risk_reward_ratio so
that downstream DB writes and dashboard rows aren't stale.

Adapted from the plan: the actual analyze_stock signature is
    analyze_stock(self, ticker: str, raw_data: Dict) -> dict
(the plan's test_stub used a (state, drop_percent, raw_data) form that
doesn't match the real method; updated here to reflect reality).
"""
from unittest.mock import patch, MagicMock

import pytest

import app.services.research_service as rs_mod
from app.services.research_service import ResearchService


# A long-enough fake string to pass _is_real_report (>200 chars, no error markers).
_STUB_REPORT = "x" * 300


def _make_raw_data():
    """raw_data that exercises the stop-guard: entry_price_low=227, ATR=12.55,
    stop from PM (222) is < 1 ATR below entry so the guard will widen it."""
    return {
        "change_percent": -7.0,
        "indicators": {
            "close": 227.0,
            "atr": 12.55,
            "sma50": 240.0,
            "sma200": 250.0,
        },
    }


def _make_pm_decision():
    """PM decision with a stop_loss that is too tight (222 is only ~2.2% below
    entry 227, but 1 ATR = 12.55 so guard would widen to ~227 - 2*12.55 = 201.9)."""
    return {
        "action": "BUY",
        "conviction": "MODERATE",
        "drop_type": "OVERREACTION",
        "entry_price_low": 227.0,
        "entry_price_high": 230.0,
        "stop_loss": 222.0,
        "take_profit_1": 250.0,
        "take_profit_2": 270.0,
        "upside_percent": 10.0,
        "downside_risk_percent": 2.2,
        "risk_reward_ratio": 4.5,
        "reason": "stub",
        "key_factors": [],
    }


def test_response_dict_uses_post_guard_risk_metrics():
    """Simulate the EXPE case: PM returns stop=222 (too tight),
    guard widens to ~201.9; upside=10%. The response dict must show R/R≈0.9
    not the PM's original 4.5."""
    rs = ResearchService.__new__(ResearchService)  # bypass __init__ deps
    rs.api_key = "stub"
    rs.model = None
    rs.flash_model = None
    rs.grounding_client = None
    import threading
    rs.lock = threading.Lock()

    raw_data = _make_raw_data()
    pm_decision = _make_pm_decision()

    # We patch at the module level for module-level objects (seeking_alpha_service,
    # evidence_service) and as instance method patches for ResearchService methods.
    with patch.object(rs, "_check_and_increment_usage", return_value=True), \
         patch.object(rs, "_call_agent", return_value=_STUB_REPORT), \
         patch.object(rs_mod, "seeking_alpha_service") as mock_sa, \
         patch.object(rs_mod, "fred_service") as mock_fred, \
         patch("app.services.quality_control_service.QualityControlService") as mock_qc, \
         patch.object(rs, "_run_bull_bear_perspectives", return_value=None), \
         patch.object(rs, "_run_risk_council_and_decision", return_value=pm_decision), \
         patch.object(rs, "_format_full_report", return_value="report"), \
         patch("app.services.evidence_service.evidence_service") as mock_ev, \
         patch("os.makedirs", return_value=None), \
         patch("builtins.open", MagicMock()):

        # seeking_alpha stubs
        mock_sa.get_evidence.return_value = _STUB_REPORT
        mock_sa.get_counts.return_value = {"total": 0, "analysis": 0, "news": 0, "pr": 0}

        # fred stub — no econ trigger needed
        mock_fred.get_macro_data.return_value = None

        # QC pass-through
        mock_qc.validate_council_reports.side_effect = lambda r, t: r
        mock_qc.validate_reports.side_effect = lambda r, t, names: r

        # evidence barometer stub
        mock_ev.collect_barometer.return_value = {}

        out = rs.analyze_stock("EXPE", raw_data)

    # Stop should have been widened by the guard (222 → ~201.9)
    assert out["stop_loss"] < 222.0, (
        f"Expected stop_loss to be widened below 222, got {out['stop_loss']}"
    )

    # R/R must be recomputed against the new (wider) stop, not the PM's stale 4.5
    assert out["risk_reward_ratio"] is not None
    assert out["risk_reward_ratio"] < 2.0, (
        f"Expected post-guard R/R < 2.0 (upside=10%, wide stop), got {out['risk_reward_ratio']}"
    )

    # Downside should reflect the wider stop (wider than PM's 2.2%)
    assert out["downside_risk_percent"] > 2.2, (
        f"Expected downside_risk_percent > 2.2 after stop widening, got {out['downside_risk_percent']}"
    )
