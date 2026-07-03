"""Tests for gate-input degeneracy monitoring (three-tier feedback, Tier 1).

The falling-knife collapse (structured verdict 97% YES, 256/264 since
2026-06-11) is the template failure: any gate input that stops discriminating
must auto-suspend its gate instead of downgrading the whole book.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.database import add_decision_point, update_decision_point


@pytest.fixture()
def fresh_db(_no_production_db):
    from app.database import init_db
    init_db()
    yield


def _add(symbol, drop_type, knife, sentiment):
    decision_id = add_decision_point(
        symbol=symbol, price=100.0, drop_percent=-6.0,
        recommendation="PENDING", reasoning="...", status="Pending",
    )
    update_decision_point(
        decision_id, "WATCH", "test", "Pending DR Review",
        drop_type=drop_type, risk_falling_knife=knife, news_sentiment=sentiment,
    )
    return decision_id


def test_recent_signal_rates(fresh_db):
    from app.database import get_recent_signal_rates
    for i in range(30):
        _add(f"T{i}", "EARNINGS_MISS" if i < 12 else "SECTOR_ROTATION",
             "YES" if i < 29 else "NO",
             "BEARISH" if i < 6 else "NEUTRAL")
    rates = get_recent_signal_rates(limit=50)
    assert rates["n"] == 30
    assert rates["drop_type_gated_rate"] == pytest.approx(12 / 30)
    assert rates["knife_n"] == 30
    assert rates["knife_yes_rate"] == pytest.approx(29 / 30)
    assert rates["news_bearish_rate"] == pytest.approx(6 / 30)


def test_recent_signal_rates_empty_db(fresh_db):
    from app.database import get_recent_signal_rates
    rates = get_recent_signal_rates()
    assert rates == {"n": 0, "drop_type_gated_rate": 0.0,
                     "news_bearish_rate": 0.0, "knife_n": 0, "knife_yes_rate": 0.0}


def test_check_degeneracy_flags_collapsed_knife():
    """The June collapse (97% YES) must trip the knife ceiling (0.60)."""
    from app.services.decision_gate_service import check_gate_degeneracy
    rates = {"n": 50, "drop_type_gated_rate": 0.35, "news_bearish_rate": 0.2,
             "knife_n": 50, "knife_yes_rate": 0.97}
    assert check_gate_degeneracy(rates) == ["RISK_KNIFE_GATE"]


def test_check_degeneracy_healthy_signals_pass():
    from app.services.decision_gate_service import check_gate_degeneracy
    rates = {"n": 50, "drop_type_gated_rate": 0.40, "news_bearish_rate": 0.25,
             "knife_n": 50, "knife_yes_rate": 0.30}
    assert check_gate_degeneracy(rates) == []


def test_check_degeneracy_needs_minimum_sample():
    from app.services.decision_gate_service import check_gate_degeneracy
    rates = {"n": 10, "drop_type_gated_rate": 1.0, "news_bearish_rate": 1.0,
             "knife_n": 10, "knife_yes_rate": 1.0}
    assert check_gate_degeneracy(rates) == []


def test_apply_gates_skips_suspended_gate(monkeypatch):
    import app.services.decision_gate_service as dgs
    monkeypatch.setattr(dgs, "_load_suspensions", lambda: frozenset({"DROP_TYPE_GATE"}))
    result = dgs.apply_decision_gates(
        action="BUY", drop_type="EARNINGS_MISS", conviction="HIGH",
        sa_quant_rating=3.5,
    )
    assert result.final_action == "BUY"
    assert result.gates_fired == []


def test_nightly_check_writes_suspension_file(tmp_path, monkeypatch):
    import app.services.decision_gate_service as dgs
    import app.database as db
    path = tmp_path / "gate_suspensions.json"
    monkeypatch.setattr(dgs, "_SUSPENSIONS_PATH", str(path))
    monkeypatch.setattr(db, "get_recent_signal_rates", lambda limit, gated_drop_types: {
        "n": 50, "drop_type_gated_rate": 0.9, "news_bearish_rate": 0.1,
        "knife_n": 50, "knife_yes_rate": 0.2,
    })
    newly = dgs.run_nightly_degeneracy_check()
    assert newly == ["DROP_TYPE_GATE"]
    assert json.loads(path.read_text())["suspended"] == ["DROP_TYPE_GATE"]
    assert dgs._load_suspensions() == frozenset({"DROP_TYPE_GATE"})
    # Second run: already suspended, so nothing is NEWLY suspended.
    assert dgs.run_nightly_degeneracy_check() == []
