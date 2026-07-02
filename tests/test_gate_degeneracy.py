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
