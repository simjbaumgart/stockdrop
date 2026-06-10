"""Gate 4 tests: DR override basis (NAMED_EVENT binding vs JUDGMENT advisory).

Soft-rationale DR overrides historically lost -5.9 pts while hard-event
overrides added +10.9 pts. These tests pin the new behavior:
  * OVERRIDDEN + JUDGMENT  -> advisory only, council action stands
  * OVERRIDDEN + NAMED_EVENT -> binding, AVOID applied
  * NAMED_EVENT positive catalyst lifts a drop-type-gated WATCH to BUY_LIMIT
  * buy-side DR on a gated row WITHOUT a named event leaves the gate standing
"""

import os
import sys

TEST_DB = "test_dr_override_basis.db"
os.environ["DB_PATH"] = TEST_DB

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database
app.database.DB_NAME = TEST_DB

import pytest

from app.database import (
    init_db,
    add_decision_point,
    update_decision_point,
    get_decision_point,
    lift_gated_watch_to_buy_limit,
)
from app.services.deep_research_service import deep_research_service


VALID_LEVELS = {
    "entry_price_low": 100.0,
    "entry_price_high": 105.0,
    "stop_loss": 92.0,
    "take_profit_1": 120.0,
    "take_profit_2": 130.0,
    "upside_percent": 15.0,
    "downside_risk_percent": 8.0,
    "risk_reward_ratio": 1.9,
}


def _dr_result(**overrides) -> dict:
    base = {
        "review_verdict": "CONFIRMED",
        "action": "BUY",
        "conviction": "HIGH",
        "drop_type": "SECTOR_ROTATION",
        "risk_level": "Medium",
        "catalyst_type": "Temporary",
        "knife_catch_warning": False,
        "reason": "test",
        "override_basis": "NONE",
        "named_event": None,
        **VALID_LEVELS,
    }
    base.update(overrides)
    return base


@pytest.fixture()
def db(monkeypatch):
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db()
    # Silence file/console side effects of _handle_completion.
    monkeypatch.setattr(deep_research_service, "_save_result_to_file",
                        lambda *a, **k: None)
    monkeypatch.setattr(deep_research_service, "_print_deep_research_result",
                        lambda *a, **k: None)
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def _make_decision(recommendation="BUY", status="Pending DR Review", **fields) -> int:
    decision_id = add_decision_point(
        symbol="DRTEST", price=110.0, drop_percent=-6.0,
        recommendation="PENDING", reasoning="Analyzing...", status="Pending",
    )
    update_decision_point(decision_id, recommendation, "reasons", status, **fields)
    return decision_id


def test_judgment_override_is_advisory_council_action_stands(db):
    decision_id = _make_decision(recommendation="BUY")
    deep_research_service._handle_completion(
        {"symbol": "DRTEST", "decision_id": decision_id},
        _dr_result(review_verdict="OVERRIDDEN", action="AVOID",
                   override_basis="JUDGMENT",
                   reason="Structurally challenged; macro headwinds."),
    )
    row = get_decision_point(decision_id)
    assert row["recommendation"] == "BUY"                       # council stands
    assert row["deep_research_verdict"] == "BUY"                # legacy column follows council
    assert row["deep_research_review_verdict"] == "OVERRIDDEN"  # truthful DR record
    assert row["deep_research_action"] == "AVOID"
    assert row["deep_research_override_basis"] == "JUDGMENT"
    assert row["status"] == "Owned"                             # BUY stands -> position taken


def test_override_without_basis_field_treated_as_judgment(db):
    # Legacy/unparsed results carry no override_basis: never binding.
    decision_id = _make_decision(recommendation="BUY_LIMIT")
    result = _dr_result(review_verdict="OVERRIDDEN", action="AVOID")
    del result["override_basis"], result["named_event"]
    deep_research_service._handle_completion(
        {"symbol": "DRTEST", "decision_id": decision_id}, result)
    row = get_decision_point(decision_id)
    assert row["recommendation"] == "BUY_LIMIT"
    assert row["deep_research_verdict"] == "BUY_LIMIT"
    assert row["status"] == "Owned"


def test_named_event_override_is_binding(db):
    decision_id = _make_decision(recommendation="BUY")
    deep_research_service._handle_completion(
        {"symbol": "DRTEST", "decision_id": decision_id},
        _dr_result(review_verdict="OVERRIDDEN", action="AVOID",
                   override_basis="NAMED_EVENT",
                   named_event="DOJ antitrust suit filed 2026-06-08"),
    )
    row = get_decision_point(decision_id)
    assert row["deep_research_verdict"] == "AVOID"
    assert row["deep_research_override_basis"] == "NAMED_EVENT"
    assert row["deep_research_named_event"] == "DOJ antitrust suit filed 2026-06-08"
    assert row["status"] == "Not Owned"


def test_named_event_lifts_gated_watch_to_buy_limit(db):
    decision_id = _make_decision(
        recommendation="WATCH",
        drop_type="EARNINGS_MISS",
        pre_gate_action="BUY",
        gates_fired="DROP_TYPE_GATE",
        gate_reasons="EARNINGS_MISS buys have no historical edge",
    )
    deep_research_service._handle_completion(
        {"symbol": "DRTEST", "decision_id": decision_id},
        _dr_result(review_verdict="UPGRADED", action="BUY",
                   override_basis="NAMED_EVENT",
                   named_event="Guidance reaffirmed in 8-K filed 2026-06-09"),
    )
    row = get_decision_point(decision_id)
    assert row["recommendation"] == "BUY_LIMIT"   # lifted, but only to BUY_LIMIT
    assert "DR_NAMED_EVENT_LIFT" in row["gates_fired"]
    assert "8-K" in row["gate_reasons"]
    assert row["status"] == "Owned"


def test_buy_side_dr_without_named_event_leaves_gate_standing(db):
    decision_id = _make_decision(
        recommendation="WATCH",
        drop_type="EARNINGS_MISS",
        pre_gate_action="BUY",
        gates_fired="DROP_TYPE_GATE",
    )
    deep_research_service._handle_completion(
        {"symbol": "DRTEST", "decision_id": decision_id},
        _dr_result(review_verdict="CONFIRMED", action="BUY",
                   override_basis="JUDGMENT", named_event=None),
    )
    row = get_decision_point(decision_id)
    assert row["recommendation"] == "WATCH"
    assert "DR_NAMED_EVENT_LIFT" not in (row["gates_fired"] or "")
    assert row["status"] == "Not Owned"


def test_lift_never_touches_organic_watch(db):
    decision_id = _make_decision(recommendation="WATCH",
                                 pre_gate_action="WATCH", gates_fired="")
    assert not lift_gated_watch_to_buy_limit(decision_id, "some event")
    row = get_decision_point(decision_id)
    assert row["recommendation"] == "WATCH"
