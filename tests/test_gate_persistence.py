"""Integration tests for the decision-gate layer: DB persistence + DR routing.

Verifies the wiring around decision_gate_service: gate fields survive the
update_decision_point round-trip, and gated-away buys still route to Deep
Research (the NAMED_EVENT re-upgrade path).
"""

import os
import sys

TEST_DB = "test_gate_persistence.db"
os.environ["DB_PATH"] = TEST_DB

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database
app.database.DB_NAME = TEST_DB

import pytest

from app.database import init_db, add_decision_point, update_decision_point, get_decision_point


@pytest.fixture()
def fresh_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def test_gate_fields_round_trip(fresh_db):
    decision_id = add_decision_point(
        symbol="GATETEST", price=100.0, drop_percent=-6.0,
        recommendation="PENDING", reasoning="Analyzing...", status="Pending",
    )
    update_decision_point(
        decision_id, "WATCH", "gated", "Pending DR Review",
        drop_type="EARNINGS_MISS",
        pre_gate_action="BUY",
        gates_fired="DROP_TYPE_GATE",
        gate_reasons="EARNINGS_MISS buys have no historical edge (37-39% win at 7d)",
    )
    row = get_decision_point(decision_id)
    assert row["recommendation"] == "WATCH"
    assert row["pre_gate_action"] == "BUY"
    assert row["gates_fired"] == "DROP_TYPE_GATE"
    assert "no historical edge" in row["gate_reasons"]


def test_ungated_decision_records_empty_gates(fresh_db):
    # "" (layer ran, nothing fired) must persist — only None is skipped.
    decision_id = add_decision_point(
        symbol="CLEANTEST", price=50.0, drop_percent=-5.5,
        recommendation="PENDING", reasoning="Analyzing...", status="Pending",
    )
    update_decision_point(
        decision_id, "BUY", "ok", "Pending DR Review",
        pre_gate_action="BUY", gates_fired="", gate_reasons="",
    )
    row = get_decision_point(decision_id)
    assert row["pre_gate_action"] == "BUY"
    assert row["gates_fired"] == ""


def test_gated_away_buy_still_triggers_deep_research():
    from app.services.stock_service import StockService
    svc = StockService.__new__(StockService)  # no heavy __init__

    # Organic buys trigger.
    assert svc._should_trigger_deep_research({"recommendation": "BUY"})
    assert svc._should_trigger_deep_research({"recommendation": "BUY_LIMIT"})
    # Gated-away buy (PM buy downgraded to WATCH) still triggers — DR's
    # NAMED_EVENT catalyst is the one path that can lift the WATCH back.
    assert svc._should_trigger_deep_research({
        "recommendation": "WATCH",
        "pre_gate_action": "BUY",
        "gates_fired": "DROP_TYPE_GATE",
    })
    # Organic WATCH/AVOID never trigger.
    assert not svc._should_trigger_deep_research({
        "recommendation": "WATCH", "pre_gate_action": "WATCH", "gates_fired": "",
    })
    assert not svc._should_trigger_deep_research({"recommendation": "AVOID"})
