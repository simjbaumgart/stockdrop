"""Tests for the semantic check on Fund Manager JSON-repair output.

Regression (NXT 2026-06-10): the PM's raw output failed to parse (truncated
string), the Gemini Flash repair "succeeded" structurally — but the saved
decision had key_factors [".", "<one factor>"] and zeroed prices. The repair
pass validated structure, not content. After repair we now run a minimal
semantic check (key_factors non-trivial strings, prices > 0, required fields
present) and re-prompt the PM once on failure rather than persisting junk.
"""
import json
from types import SimpleNamespace

import app.services.research_service as rs
from app.services.research_service import ResearchService, _fm_semantic_check


GOOD_DECISION = {
    "action": "BUY_LIMIT",
    "conviction": "MODERATE",
    "entry_price_low": 100.0,
    "entry_price_high": 105.0,
    "stop_loss": 92.0,
    "take_profit_1": 120.0,
    "take_profit_2": None,
    "reason": "Oversold on sector rotation, fundamentals intact.",
    "key_factors": ["Earnings beat last quarter", "RSI at 22, deeply oversold"],
}


# --- _fm_semantic_check ---

def test_good_decision_passes():
    ok, _ = _fm_semantic_check(GOOD_DECISION)
    assert ok


def test_trivial_key_factor_fails():
    # The exact NXT shape: a bare "." survived repair.
    bad = dict(GOOD_DECISION, key_factors=[".", "Earnings beat last quarter"])
    ok, reason = _fm_semantic_check(bad)
    assert not ok
    assert "key_factors" in reason


def test_empty_key_factors_tolerated():
    # A payload truncated BEFORE key_factors (NIO 2026-05-22) repairs to an
    # empty list — that's honest truncation, not degraded content, and must
    # not burn a PM re-prompt (see test_fund_manager_failure_path.py).
    ok, _ = _fm_semantic_check(dict(GOOD_DECISION, key_factors=[]))
    assert ok


def test_missing_key_factors_tolerated():
    good = {k: v for k, v in GOOD_DECISION.items() if k != "key_factors"}
    ok, _ = _fm_semantic_check(good)
    assert ok


def test_non_list_key_factors_fails():
    ok, _ = _fm_semantic_check(dict(GOOD_DECISION, key_factors="just a string"))
    assert not ok


def test_zeroed_price_fails():
    ok, reason = _fm_semantic_check(dict(GOOD_DECISION, entry_price_low=0.0))
    assert not ok
    assert "entry_price_low" in reason


def test_negative_price_fails():
    ok, _ = _fm_semantic_check(dict(GOOD_DECISION, stop_loss=-5.0))
    assert not ok


def test_null_optional_price_is_fine():
    # take_profit_2 is nullable in the schema; None must not fail the check.
    ok, _ = _fm_semantic_check(GOOD_DECISION)
    assert ok


def test_missing_action_fails():
    bad = {k: v for k, v in GOOD_DECISION.items() if k != "action"}
    ok, _ = _fm_semantic_check(bad)
    assert not ok


# --- re-prompt flow ---

def _svc():
    svc = ResearchService.__new__(ResearchService)  # bypass __init__/network
    svc.api_key = "test-key"
    return svc


def _wire(monkeypatch, svc, agent_outputs, repair_result):
    """Wire a fake PM (_call_agent returns agent_outputs in order) and a fake
    Flash repair into _run_risk_council_and_decision."""
    calls = {"agent": 0}

    def fake_call_agent(prompt, agent_name, state=None, **kw):
        out = agent_outputs[min(calls["agent"], len(agent_outputs) - 1)]
        calls["agent"] += 1
        return out

    monkeypatch.setattr(svc, "_call_agent", fake_call_agent, raising=False)
    monkeypatch.setattr(
        svc, "_create_fund_manager_prompt", lambda *a, **k: "PROMPT", raising=False
    )
    monkeypatch.setattr(rs, "repair_json_via_flash", lambda *a, **k: repair_result)
    monkeypatch.setattr(
        rs, "agent_call_counter", SimpleNamespace(record=lambda *a, **k: None)
    )
    return calls


def test_junk_repair_triggers_single_pm_reprompt(monkeypatch):
    svc = _svc()
    junk_repair = dict(GOOD_DECISION, key_factors=["."])
    state = SimpleNamespace(reports={}, ticker="NXT")
    calls = _wire(
        monkeypatch, svc,
        agent_outputs=['{"truncated...', json.dumps(GOOD_DECISION)],
        repair_result=junk_repair,
    )

    decision = svc._run_risk_council_and_decision(state, "-7.5%")

    assert calls["agent"] == 2  # initial + exactly one re-prompt
    assert decision["key_factors"] == GOOD_DECISION["key_factors"]
    assert decision["action"] == "BUY_LIMIT"


def test_junk_repair_and_failed_reprompt_yields_no_decision(monkeypatch):
    svc = _svc()
    junk_repair = dict(GOOD_DECISION, key_factors=["."])
    state = SimpleNamespace(reports={}, ticker="NXT")
    _wire(
        monkeypatch, svc,
        agent_outputs=['{"truncated...', '{"still truncated...'],
        repair_result=junk_repair,
    )

    decision = svc._run_risk_council_and_decision(state, "-7.5%")

    # Junk must never persist: PASS_INSUFFICIENT_DATA, not the "." factors.
    assert decision["action"] == "PASS_INSUFFICIENT_DATA"


def test_clean_repair_still_accepted_without_reprompt(monkeypatch):
    svc = _svc()
    state = SimpleNamespace(reports={}, ticker="NXT")
    calls = _wire(
        monkeypatch, svc,
        agent_outputs=['{"truncated...'],
        repair_result=dict(GOOD_DECISION),
    )

    decision = svc._run_risk_council_and_decision(state, "-7.5%")

    assert calls["agent"] == 1  # no re-prompt needed
    assert decision["action"] == "BUY_LIMIT"
