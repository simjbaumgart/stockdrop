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
