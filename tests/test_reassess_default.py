"""Phase D plumbing: buys default to a 4-week reassess cadence when the PM
omits reassess_in_days (the edge materializes at ~4 weeks, not 1)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_buy_actions_default_to_four_weeks():
    from app.services.research_service import (
        REASSESS_DEFAULT_TRADING_DAYS, _reassess_in_days_with_default)
    assert REASSESS_DEFAULT_TRADING_DAYS == 20
    assert _reassess_in_days_with_default(None, "BUY") == 20
    assert _reassess_in_days_with_default(None, "BUY_LIMIT") == 20
    assert _reassess_in_days_with_default(0, "BUY") == 20


def test_pm_value_and_non_buys_pass_through():
    from app.services.research_service import _reassess_in_days_with_default
    assert _reassess_in_days_with_default(5, "BUY") == 5
    assert _reassess_in_days_with_default(None, "WATCH") is None
    assert _reassess_in_days_with_default(None, "AVOID") is None
    assert _reassess_in_days_with_default(7, "WATCH") == 7


def test_is_due_uses_cadence_and_last_reassess():
    import datetime
    from scripts.reassess_positions import _is_due
    today = datetime.date(2026, 7, 2)

    # Decision 30 calendar days ago, default cadence (20td ≈ 28cd) -> due.
    assert _is_due({"timestamp": "2026-06-02 10:00:00",
                    "reassess_timestamp": None, "reassess_in_days": None}, today)
    # Decision 10 days ago -> not due yet.
    assert not _is_due({"timestamp": "2026-06-22 10:00:00",
                        "reassess_timestamp": None, "reassess_in_days": None}, today)
    # PM said 5 trading days (=> 7 calendar): 8 days ago -> due.
    assert _is_due({"timestamp": "2026-06-24 10:00:00",
                    "reassess_timestamp": None, "reassess_in_days": 5}, today)
    # Reassessed yesterday resets the clock -> not due.
    assert not _is_due({"timestamp": "2026-06-02 10:00:00",
                        "reassess_timestamp": "2026-07-01 10:00:00",
                        "reassess_in_days": None}, today)
    # Unparseable timestamp -> due (surface it rather than silently skip).
    assert _is_due({"timestamp": "garbage", "reassess_timestamp": None,
                    "reassess_in_days": None}, today)
