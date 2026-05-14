"""Verify that the wall-clock budget resets when wall-clock time advances
much faster than monotonic time (i.e., laptop slept)."""

import os
import sys
import time
from unittest.mock import patch

os.environ.setdefault("DB_PATH", "test_budget_sleep.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.research_service import (
    BudgetClock,
    AGENT_WALL_CLOCK_BUDGET_SEC,
    SLEEP_DETECTION_THRESHOLD_SEC,
)


def test_clock_does_not_reset_on_normal_progress():
    bc = BudgetClock(now=1_000.0, monotonic=10.0)
    # 5 seconds of normal progress.
    bc.tick(now=1_005.0, monotonic=15.0)
    assert bc.deadline == 1_000.0 + AGENT_WALL_CLOCK_BUDGET_SEC


def test_clock_resets_after_sleep_gap():
    bc = BudgetClock(now=1_000.0, monotonic=10.0)
    # 1h sleep: wall clock jumps ~3600s but monotonic only +1s (machine slept).
    bc.tick(now=4_600.0, monotonic=11.0)
    assert bc.deadline == 4_600.0 + AGENT_WALL_CLOCK_BUDGET_SEC, "deadline must be re-stamped after sleep"


def test_clock_threshold_is_meaningful():
    assert SLEEP_DETECTION_THRESHOLD_SEC >= 60
