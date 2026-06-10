"""Unit tests for the deterministic post-PM decision gates."""

import pytest

from app.services.decision_gate_service import (
    GATED_DROP_TYPES,
    GateResult,
    apply_decision_gates,
    risk_report_flags_knife,
)


KNIFE_REPORT = "Trap Check\nVerdict: YES — this is a falling knife, momentum is broken."
KNIFE_REPORT_PROSE = "Trap Check verdict — in our view a falling knife scenario."
CLEAN_REPORT = "Trap Check\nVerdict: NO. Orderly pullback, support held."


# ---------------------------------------------------------------------------
# Gate 1: drop_type
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("drop_type", sorted(GATED_DROP_TYPES))
@pytest.mark.parametrize("action", ["BUY", "BUY_LIMIT"])
def test_gate1_downgrades_gated_drop_types(action, drop_type):
    r = apply_decision_gates(action, drop_type, "HIGH", None)
    assert r.final_action == "WATCH"
    assert r.pre_gate_action == action
    assert r.gates_fired == ["DROP_TYPE_GATE"]
    assert r.gate_reasons  # human-readable reason recorded


@pytest.mark.parametrize("drop_type", ["SECTOR_ROTATION", "MACRO_SELLOFF",
                                       "TECHNICAL_BREAKDOWN", "UNKNOWN", None, ""])
def test_gate1_passthrough_drop_types(drop_type):
    r = apply_decision_gates("BUY", drop_type, "HIGH", None)
    assert r.final_action == "BUY"
    assert r.gates_fired == []


def test_gate1_case_insensitive_drop_type():
    r = apply_decision_gates("buy", "earnings_miss", "HIGH", None)
    assert r.final_action == "WATCH"
    assert r.gates_fired == ["DROP_TYPE_GATE"]


# ---------------------------------------------------------------------------
# Gate 2: SA quant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["BUY", "BUY_LIMIT"])
def test_gate2_low_quant_downgrades(action):
    r = apply_decision_gates(action, "SECTOR_ROTATION", "HIGH", 2.49)
    assert r.final_action == "WATCH"
    assert r.gates_fired == ["SA_QUANT_GATE"]


@pytest.mark.parametrize("quant", [2.5, 3.5, 4.99])
def test_gate2_quant_at_or_above_floor_passes(quant):
    r = apply_decision_gates("BUY", "SECTOR_ROTATION", "HIGH", quant)
    assert r.final_action == "BUY"
    assert r.gates_fired == []


def test_gate2_missing_quant_does_not_block():
    r = apply_decision_gates("BUY", "SECTOR_ROTATION", "HIGH", None)
    assert r.final_action == "BUY"
    assert r.gates_fired == []


# ---------------------------------------------------------------------------
# Gate 3: risk knife (interim regex + structured field)
# ---------------------------------------------------------------------------

def test_gate3_knife_buy_downgrades_to_buy_limit():
    r = apply_decision_gates("BUY", "SECTOR_ROTATION", "HIGH", None,
                             risk_report=KNIFE_REPORT)
    assert r.final_action == "BUY_LIMIT"
    assert r.gates_fired == ["RISK_KNIFE_GATE"]


def test_gate3_knife_with_low_conviction_goes_to_watch():
    r = apply_decision_gates("BUY", "SECTOR_ROTATION", "LOW", None,
                             risk_report=KNIFE_REPORT)
    assert r.final_action == "WATCH"
    assert r.gates_fired == ["RISK_KNIFE_GATE"]


def test_gate3_does_not_fire_on_buy_limit():
    # Per plan: knife gate applies to outright BUY only.
    r = apply_decision_gates("BUY_LIMIT", "SECTOR_ROTATION", "HIGH", None,
                             risk_report=KNIFE_REPORT)
    assert r.final_action == "BUY_LIMIT"
    assert r.gates_fired == []


def test_gate3_structured_field_takes_precedence_over_report():
    # Structured NO wins even when the free text would match.
    r = apply_decision_gates("BUY", "SECTOR_ROTATION", "HIGH", None,
                             risk_report=KNIFE_REPORT, risk_falling_knife="NO")
    assert r.gates_fired == []
    # Structured YES fires without any report text.
    r = apply_decision_gates("BUY", "SECTOR_ROTATION", "HIGH", None,
                             risk_falling_knife="YES")
    assert r.final_action == "BUY_LIMIT"
    assert r.gates_fired == ["RISK_KNIFE_GATE"]


def test_knife_regex_matches_explicit_verdicts_only():
    assert risk_report_flags_knife(KNIFE_REPORT)
    assert risk_report_flags_knife(KNIFE_REPORT_PROSE)
    assert not risk_report_flags_knife(CLEAN_REPORT)
    # Generic knife discussion without a verdict marker must NOT fire.
    assert not risk_report_flags_knife("This could be a falling knife if support breaks.")
    assert not risk_report_flags_knife(None)
    assert not risk_report_flags_knife("")


# ---------------------------------------------------------------------------
# Combinations + non-buy passthrough
# ---------------------------------------------------------------------------

def test_multiple_gates_record_all_and_take_most_restrictive():
    r = apply_decision_gates("BUY", "EARNINGS_MISS", "HIGH", 2.0,
                             risk_report=KNIFE_REPORT)
    assert r.final_action == "WATCH"  # WATCH outranks BUY_LIMIT
    assert r.gates_fired == ["DROP_TYPE_GATE", "SA_QUANT_GATE", "RISK_KNIFE_GATE"]
    assert len(r.gate_reasons) == 3


def test_knife_only_on_buy_limit_with_low_quant():
    r = apply_decision_gates("BUY_LIMIT", "MACRO_SELLOFF", "LOW", 1.9)
    assert r.final_action == "WATCH"
    assert r.gates_fired == ["SA_QUANT_GATE"]


@pytest.mark.parametrize("action", ["WATCH", "AVOID", "", None])
def test_non_buy_actions_pass_through(action):
    r = apply_decision_gates(action, "EARNINGS_MISS", "LOW", 1.0,
                             risk_report=KNIFE_REPORT)
    assert r.final_action == (action or "").upper()
    assert r.gates_fired == []
    assert r.gate_reasons == []


def test_result_preserves_pre_gate_action_for_ab():
    r = apply_decision_gates("BUY_LIMIT", "COMPANY_SPECIFIC", "MEDIUM", None)
    assert isinstance(r, GateResult)
    assert r.pre_gate_action == "BUY_LIMIT"
    assert r.final_action == "WATCH"
